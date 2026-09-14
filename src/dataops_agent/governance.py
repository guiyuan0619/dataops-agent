from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from dataops_agent.agent_tools import offline_governance_tool_calls, validate_tool_calls
from dataops_agent.consumer_gate import evaluate_consumer_gate
from dataops_agent.decision_memory import GovernanceDecisionMemoryStore
from dataops_agent.repair import apply_plan, build_repair_plan, preview_plan
from dataops_agent.rules import (
    DATASET_ALIASES,
    DATASET_SCHEMA_VERSIONS,
    detect_issues,
    load_csv,
    profile_dataframe,
    quality_report,
)
from dataops_agent.schemas import RepairPlan
from dataops_agent.semantic_planner import configured_semantic_planner, plan_semantic_repairs
from dataops_agent.versioning import ActiveVersionConflictError, VersionStore


class GovernanceState(TypedDict, total=False):
    thread_id: str
    dataset_name: str
    dataset_path: str
    version_db_path: str
    artifact_dir: str
    profile: dict[str, Any]
    issues: list[dict[str, Any]]
    plan: dict[str, Any]
    preview: dict[str, Any]
    candidate_checksum: str
    expected_candidate_checksum: str
    actual_candidate_checksum: str
    expected_active_version_id: str | None
    actual_active_version_id: str | None
    tool_calls: list[dict[str, Any]]
    approved: bool
    approver: str
    status: str
    quality_report: dict[str, Any]
    consumer_regression: dict[str, Any]
    version: dict[str, Any]
    unresolved_issue_count: int
    semantic_planning: dict[str, Any]
    plan_checksum: str
    contract_version: str
    decision_memory_enabled: bool
    decision_memory_recall: dict[str, Any]
    decision_memory_write: dict[str, Any]
    decision_reason: str


def start_governance_run(
    *,
    thread_id: str,
    dataset_name: str,
    dataset_path: str | Path,
    checkpoint_path: str | Path,
    version_db_path: str | Path,
    artifact_dir: str | Path,
    planner_provider: Any | None = None,
    decision_memory_enabled: bool = True,
) -> dict[str, Any]:
    state: GovernanceState = {
        "thread_id": thread_id,
        "dataset_name": dataset_name,
        "dataset_path": str(dataset_path),
        "version_db_path": str(version_db_path),
        "artifact_dir": str(artifact_dir),
        "decision_memory_enabled": decision_memory_enabled,
    }
    with _compiled_graph(checkpoint_path, planner_provider=planner_provider) as graph:
        if graph.get_state(_thread_config(thread_id)).values:
            raise ValueError("Governance thread already exists; resume it or use a new thread_id.")
        result = graph.invoke(state, config=_thread_config(thread_id))
    return _public_result(result)


def resume_governance_run(
    *,
    thread_id: str,
    approved: bool,
    checkpoint_path: str | Path,
    approver: str = "local_user",
    decision_reason: str = "",
    rejected_mappings: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    with _compiled_graph(checkpoint_path) as graph:
        result = graph.invoke(
            Command(resume={
                "approved": approved,
                "approver": approver,
                "decision_reason": decision_reason,
                "rejected_mappings": rejected_mappings or [],
            }),
            config=_thread_config(thread_id),
        )
    return _public_result(result)


def thread_status(*, thread_id: str, checkpoint_path: str | Path) -> dict[str, Any]:
    with _compiled_graph(checkpoint_path) as graph:
        snapshot = graph.get_state(_thread_config(thread_id))
    values = dict(snapshot.values) if snapshot.values else {}
    if snapshot.interrupts:
        values["status"] = "awaiting_approval"
        values["interrupt"] = snapshot.interrupts[0].value
    return _public_result(values)


def reconcile_governance_memory(
    *, thread_id: str, checkpoint_path: str | Path
) -> dict[str, Any]:
    state = thread_status(thread_id=thread_id, checkpoint_path=checkpoint_path)
    if state.get("status") != "published":
        raise ValueError("Only a published governance run can reconcile approved memory.")
    return _persist_approved_memory(state)


def _plan_tools(state: GovernanceState) -> GovernanceState:
    calls = offline_governance_tool_calls(include_write=False)
    return {"tool_calls": [call.model_dump() for call in calls], "status": "planned"}


def _analyze_dataset(state: GovernanceState, planner_provider=None) -> GovernanceState:
    dataset_path = Path(state["dataset_path"])
    initial_checksum = _file_checksum(dataset_path)
    df = load_csv(str(dataset_path))
    store = VersionStore(Path(state["version_db_path"]), Path(state["artifact_dir"]))
    active = store.active_version(state["dataset_name"])
    issues = detect_issues(df, dataset_name=state["dataset_name"])
    plan = build_repair_plan(df, issues, dataset_name=state["dataset_name"])
    dataset = DATASET_ALIASES.get(state["dataset_name"], state["dataset_name"])
    contract_version = DATASET_SCHEMA_VERSIONS[dataset]
    memory_recall: dict[str, Any] = {
        "dataset_name": state["dataset_name"],
        "contract_version": contract_version,
        "approved": [],
        "rejected": [],
        "recalled_count": 0,
        "ignored": {},
        "status": "disabled",
    }
    if state.get("decision_memory_enabled", True):
        memory_recall = GovernanceDecisionMemoryStore(
            Path(state["version_db_path"])
        ).recall(
            dataset_name=state["dataset_name"],
            contract_version=contract_version,
            groups=_semantic_memory_groups(df, issues),
        )
        memory_recall["status"] = "recalled"
    provider = (
        planner_provider
        if planner_provider is not None
        else configured_semantic_planner()
    )
    plan, semantic = plan_semantic_repairs(
        df, issues, plan, dataset_name=state["dataset_name"],
        provider=provider, decision_memory=memory_recall,
    )
    if _file_checksum(dataset_path) != initial_checksum:
        raise ValueError("Candidate changed during planning; start a new run.")
    preview = preview_plan(df, plan)
    return {
        "profile": profile_dataframe(
            df, dataset_name=state["dataset_name"]
        ).model_dump(),
        "issues": [issue.model_dump(mode="json") for issue in issues],
        "plan": plan.model_dump(mode="json"),
        "preview": preview.model_dump(mode="json"),
        "candidate_checksum": initial_checksum,
        "plan_checksum": _plan_checksum(plan),
        "semantic_planning": semantic,
        "contract_version": contract_version,
        "decision_memory_recall": _memory_recall_summary(memory_recall),
        "expected_active_version_id": active.version_id if active else None,
        "status": "preview_ready",
    }


def _approval_gate(state: GovernanceState) -> GovernanceState:
    plan = RepairPlan.model_validate(state["plan"])
    semantic = state.get("semantic_planning", {})
    if semantic.get("status") in {"manual_review", "fallback"}:
        return {"approved": False, "status": "manual_review_required", "approver": "system"}
    issue_count = len(state.get("issues", []))
    if issue_count > len(plan.actions):
        return {
            "approved": False,
            "status": "manual_review_required",
            "approver": "system",
            "unresolved_issue_count": issue_count - len(plan.actions),
        }
    if not plan.actions:
        return {"approved": True, "status": "approved_without_changes", "approver": "system"}
    resume = interrupt(
        {
            "status": "awaiting_approval",
            "dataset_name": state["dataset_name"],
            "preview": state["preview"],
            "candidate_checksum": state.get("candidate_checksum"),
            "expected_active_version_id": state.get("expected_active_version_id"),
            "tool_calls": state["tool_calls"],
            "semantic_planning": state.get("semantic_planning", {}),
            "plan_checksum": state.get("plan_checksum"),
        }
    )
    approval = _approval_payload(resume)
    if not approval["approved"]:
        memory_write = _persist_rejected_memory(state, approval)
        return {
            "approved": False,
            "approver": approval["approver"],
            "decision_reason": approval["decision_reason"],
            "decision_memory_write": memory_write,
            "status": "denied",
        }
    return {
        "approved": True,
        "approver": approval["approver"],
        "decision_reason": approval["decision_reason"],
        "status": "approved",
    }


def _execute_publish(state: GovernanceState) -> GovernanceState:
    validate_tool_calls(offline_governance_tool_calls(include_write=True))
    dataset_path = Path(state["dataset_path"])
    expected_checksum = state.get("candidate_checksum")
    actual_checksum = _file_checksum(dataset_path)
    if expected_checksum and actual_checksum != expected_checksum:
        return {
            "status": "candidate_changed_after_approval",
            "expected_candidate_checksum": expected_checksum,
            "actual_candidate_checksum": actual_checksum,
        }
    df = load_csv(str(dataset_path))
    plan = RepairPlan.model_validate(state["plan"])
    if state.get("plan_checksum") and state["plan_checksum"] != _plan_checksum(plan):
        return {"status": "plan_changed_after_approval"}
    repaired = apply_plan(df, plan)
    report = quality_report(repaired, dataset_name=state["dataset_name"])
    if not report["passes_quality_gate"]:
        return {"quality_report": report, "status": "quality_gate_failed"}
    store = VersionStore(Path(state["version_db_path"]), Path(state["artifact_dir"]))
    active = store.active_version(state["dataset_name"])
    consumer_gate = evaluate_consumer_gate(
        dataset_name=state["dataset_name"],
        candidate=repaired,
        quality_report=report,
        baseline=load_csv(str(active.path)) if active else None,
    )
    if not consumer_gate["release_gate_passed"]:
        return {
            "quality_report": report,
            "consumer_regression": consumer_gate,
            "status": "consumer_regression_failed",
        }
    expected_active_version_id = state.get("expected_active_version_id")
    try:
        record = store.publish(
            dataset_name=state["dataset_name"],
            df=repaired,
            quality_report=report,
            approved=bool(state["approved"]),
            parent_version_id=expected_active_version_id,
            expected_active_version_id=expected_active_version_id,
            require_expected_active_match=True,
        )
    except ActiveVersionConflictError as exc:
        active = store.active_version(state["dataset_name"])
        return {
            "quality_report": report,
            "consumer_regression": consumer_gate,
            "expected_active_version_id": expected_active_version_id,
            "actual_active_version_id": active.version_id if active else None,
            "status": "active_version_changed_before_publish",
            "error": str(exc),
        }
    result: GovernanceState = {
        "quality_report": report,
        "consumer_regression": consumer_gate,
        "version": record.model_dump(),
        "status": "published",
    }
    result["decision_memory_write"] = _persist_approved_memory({**state, **result})
    return result


def _route_after_approval(state: GovernanceState) -> str:
    return "execute_publish" if state.get("approved") else END


def _approval_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {
            "approved": value,
            "approver": "local_user",
            "decision_reason": "",
            "rejected_mappings": [],
        }
    if isinstance(value, dict):
        return {
            "approved": value.get("approved") is True,
            "approver": str(value.get("approver") or "local_user"),
            "decision_reason": str(value.get("decision_reason") or "").strip(),
            "rejected_mappings": value.get("rejected_mappings") or [],
        }
    return {
        "approved": False,
        "approver": "local_user",
        "decision_reason": "",
        "rejected_mappings": [],
    }


@contextmanager
def _compiled_graph(checkpoint_path: str | Path, planner_provider=None):
    Path(checkpoint_path).parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        saver.setup()
        graph = StateGraph(GovernanceState)
        graph.add_node("plan_tools", _plan_tools)
        graph.add_node("analyze_dataset", lambda state: _analyze_dataset(state, planner_provider))
        graph.add_node("approval_gate", _approval_gate)
        graph.add_node("execute_publish", _execute_publish)
        graph.add_edge(START, "plan_tools")
        graph.add_edge("plan_tools", "analyze_dataset")
        graph.add_edge("analyze_dataset", "approval_gate")
        graph.add_conditional_edges(
            "approval_gate",
            _route_after_approval,
            {"execute_publish": "execute_publish", END: END},
        )
        graph.add_edge("execute_publish", END)
        yield graph.compile(checkpointer=saver)


def _thread_config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _file_checksum(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _plan_checksum(plan: RepairPlan) -> str:
    return sha256(plan.model_dump_json().encode()).hexdigest()


def _semantic_memory_groups(df, issues) -> list[tuple[str, str]]:
    groups: set[tuple[str, str]] = set()
    for issue in issues:
        if issue.row_index not in df.index or issue.field not in df.columns:
            continue
        groups.add((issue.field, str(df.at[issue.row_index, issue.field])))
    return sorted(groups)


def _memory_recall_summary(memory: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": memory.get("status", "unknown"),
        "contract_version": memory.get("contract_version"),
        "approved_count": len(memory.get("approved", [])),
        "rejected_count": len(memory.get("rejected", [])),
        "recalled_count": memory.get("recalled_count", 0),
        "ignored": memory.get("ignored", {}),
    }


def _persist_approved_memory(state: dict[str, Any]) -> dict[str, Any]:
    if not state.get("decision_memory_enabled", True):
        return {"status": "disabled", "recorded_count": 0}
    mappings = state.get("semantic_planning", {}).get("mapping_evidence", [])
    if not mappings:
        return {"status": "no_semantic_mappings", "recorded_count": 0}
    version = state.get("version", {})
    if state.get("status") != "published" or not version.get("version_id"):
        return {"status": "not_published", "recorded_count": 0}
    try:
        records = GovernanceDecisionMemoryStore(
            Path(state["version_db_path"])
        ).record_approved_mappings(
            dataset_name=state["dataset_name"],
            contract_version=state.get("contract_version")
            or DATASET_SCHEMA_VERSIONS[
                DATASET_ALIASES.get(state["dataset_name"], state["dataset_name"])
            ],
            mappings=mappings,
            thread_id=state.get("thread_id", "") or state.get("run_id", "") or "unknown",
            approver=state.get("approver", ""),
            plan_checksum=state.get("plan_checksum", ""),
            published_version_id=version["version_id"],
        )
        return {
            "status": "recorded", "recorded_count": len(records),
            "decision_ids": [item["decision_id"] for item in records],
        }
    except Exception as exc:
        return {
            "status": "write_failed", "recorded_count": 0,
            "error_type": type(exc).__name__, "reconcile_available": True,
        }


def _persist_rejected_memory(
    state: GovernanceState, approval: dict[str, Any]
) -> dict[str, Any]:
    if not state.get("decision_memory_enabled", True):
        return {"status": "disabled", "recorded_count": 0}
    mappings = state.get("semantic_planning", {}).get("mapping_evidence", [])
    if not mappings:
        return {"status": "no_semantic_mappings", "recorded_count": 0}
    if not approval["decision_reason"]:
        return {"status": "not_explicit", "recorded_count": 0}
    requested = {
        (
            str(item.get("field", "")),
            str(item.get("source_value", "")),
            str(item.get("target_value", "")),
        )
        for item in approval.get("rejected_mappings", [])
    }
    available = {
        (
            str(item.get("field", "")),
            str(item.get("source_value", "")),
            str(item.get("target_value", "")),
        ): item
        for item in mappings
    }
    if requested:
        if not requested.issubset(available):
            return {"status": "invalid_scope", "recorded_count": 0}
        mappings = [available[key] for key in sorted(requested)]
    elif len(available) == 1:
        mappings = list(available.values())
    else:
        return {"status": "ambiguous_scope", "recorded_count": 0}
    try:
        records = GovernanceDecisionMemoryStore(
            Path(state["version_db_path"])
        ).record_rejected_mappings(
            dataset_name=state["dataset_name"],
            contract_version=state.get("contract_version")
            or DATASET_SCHEMA_VERSIONS[
                DATASET_ALIASES.get(state["dataset_name"], state["dataset_name"])
            ],
            mappings=mappings,
            thread_id=state.get("thread_id", "") or "unknown",
            approver=approval["approver"],
            reason=approval["decision_reason"],
            plan_checksum=state.get("plan_checksum", ""),
        )
        return {
            "status": "recorded", "recorded_count": len(records),
            "decision_ids": [item["decision_id"] for item in records],
        }
    except Exception as exc:
        return {
            "status": "write_failed", "recorded_count": 0,
            "error_type": type(exc).__name__,
        }


def _public_result(result: dict[str, Any]) -> dict[str, Any]:
    if "__interrupt__" not in result:
        return result
    interrupts = result["__interrupt__"]
    first = interrupts[0].value if interrupts else {}
    public = {key: value for key, value in result.items() if key != "__interrupt__"}
    public["status"] = "awaiting_approval"
    public["interrupt"] = first
    return public
