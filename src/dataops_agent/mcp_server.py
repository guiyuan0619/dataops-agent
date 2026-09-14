from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from mcp.server.fastmcp import FastMCP

from dataops_agent.audit import IntegrationAuditStore
from dataops_agent.contracts import dataset_contract as build_dataset_contract
from dataops_agent.governance import thread_status
from dataops_agent.integration import (
    GENERIC_CONTRACT_NAME,
    start_consumer_governance,
)
from dataops_agent.repair import build_repair_plan, preview_plan
from dataops_agent.rules import (
    DATASET_ALIASES,
    REQUIRED_COLUMNS_BY_DATASET,
    detect_issues,
    load_csv,
    profile_dataframe,
    quality_report,
)
from dataops_agent.skill_registry import skill_registry_snapshot
from dataops_agent.versioning import VersionStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORE = VersionStore(
    PROJECT_ROOT / "data" / "dataops.sqlite3",
    PROJECT_ROOT / "data" / "versions",
)
DEFAULT_AUDIT_STORE = IntegrationAuditStore(PROJECT_ROOT / "data" / "dataops.sqlite3")
DEFAULT_CHECKPOINT_PATH = PROJECT_ROOT / "data" / "agent_checkpoints.sqlite3"

mcp = FastMCP(
    "dataops-agent",
    instructions=(
        "Governed DataOps tools for dataset discovery, contracts, diagnostics, "
        "repair previews, approval-pending governance runs, version status, "
        "and trace lookup. Approval, publishing, rollback, shell, and arbitrary "
        "writes are not exposed through MCP."
    ),
)


@mcp.tool(name="dataops_list_datasets")
def list_datasets() -> dict[str, Any]:
    """Return supported datasets, contracts, and active-version summaries."""

    datasets = []
    for dataset_name in sorted(REQUIRED_COLUMNS_BY_DATASET):
        active = DEFAULT_STORE.active_version(dataset_name)
        versions = DEFAULT_STORE.list_versions(dataset_name)
        datasets.append(
            {
                **_dataset_contract(dataset_name),
                "active_version_id": active.version_id if active else None,
                "version_count": len(versions),
            }
        )
    return {
        "contract": GENERIC_CONTRACT_NAME,
        "datasets": datasets,
        "mcp_boundary": _mcp_boundary(),
    }


@mcp.tool(name="dataops_get_dataset_contract")
def readonly_dataset_contract(dataset_name: str) -> dict[str, Any]:
    """Return schema, key, enum, and consumer-contract rules for a dataset."""

    return {
        **_dataset_contract(_dataset_key(dataset_name)),
        "mcp_boundary": _mcp_boundary(),
    }


@mcp.tool(name="dataops_profile_dataset")
def readonly_profile_dataset(path: str, dataset_name: str = "doctors") -> dict[str, Any]:
    """Return a dataset profile for a CSV file inside the project root."""

    df = load_csv(str(_safe_project_path(path)))
    return profile_dataframe(df, dataset_name=dataset_name).model_dump()


@mcp.tool(name="dataops_validate_dataset")
def readonly_validate_dataset(path: str, dataset_name: str = "doctors") -> dict[str, Any]:
    """Return deterministic quality checks for a CSV file inside the project root."""

    df = load_csv(str(_safe_project_path(path)))
    return quality_report(df, dataset_name=dataset_name)


@mcp.tool(name="dataops_preview_repair")
def readonly_preview_repair(path: str, dataset_name: str = "doctors") -> dict[str, Any]:
    """Return repair preview for a project CSV without applying or publishing it."""

    df = load_csv(str(_safe_project_path(path)))
    dataset = _dataset_key(dataset_name)
    issues = detect_issues(df, dataset_name=dataset)
    plan = build_repair_plan(df, issues, dataset_name=dataset)
    preview = preview_plan(df, plan)
    return {
        "dataset_name": dataset,
        "profile": profile_dataframe(df, dataset_name=dataset).model_dump(),
        "quality_report": quality_report(df, dataset_name=dataset),
        "issue_count": len(issues),
        "repair_action_count": len(plan.actions),
        "unresolved_issue_count": max(0, len(issues) - len(plan.actions)),
        "preview": preview.model_dump(mode="json"),
        "mcp_boundary": _mcp_boundary(),
    }


@mcp.tool(name="dataops_start_governance_run")
def controlled_start_governance_run(
    path: str,
    dataset_name: str = "doctors",
    thread_id: str | None = None,
    consumer_agent: str = "mcp_client",
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Create an approval-pending governance run without approving or publishing."""

    thread = thread_id or f"mcp-{uuid4().hex[:12]}"
    result = start_consumer_governance(
        thread_id=thread,
        dataset_name=_dataset_key(dataset_name),
        dataset_path=_safe_project_path(path),
        checkpoint_path=DEFAULT_CHECKPOINT_PATH,
        store=DEFAULT_STORE,
        consumer_agent=consumer_agent,
        audit_store=DEFAULT_AUDIT_STORE,
        trace_id=trace_id,
    )
    result["mcp_boundary"] = _mcp_boundary()
    return result


@mcp.tool(name="dataops_governance_status")
def readonly_governance_status(thread_id: str) -> dict[str, Any]:
    """Return stored governance state for a thread without resuming approval."""

    result = thread_status(thread_id=thread_id, checkpoint_path=DEFAULT_CHECKPOINT_PATH)
    result["thread_id"] = thread_id
    result["mcp_boundary"] = _mcp_boundary()
    return result


@mcp.tool(name="dataops_version_status")
def readonly_version_status(dataset_name: str) -> dict[str, Any]:
    """Return active version and known version ids for a dataset."""

    active = DEFAULT_STORE.active_version(dataset_name)
    versions = DEFAULT_STORE.list_versions(dataset_name)
    return {
        "dataset_name": dataset_name,
        "active_version": active.model_dump() if active else None,
        "version_ids": [record.version_id for record in versions],
    }


@mcp.tool(name="dataops_get_trace")
def readonly_get_trace(
    trace_id: str | None = None,
    thread_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Return integration audit events for a trace id or governance thread id."""

    if trace_id is None and thread_id is None:
        raise ValueError("trace_id or thread_id is required.")
    events = DEFAULT_AUDIT_STORE.list_events(trace_id=trace_id, thread_id=thread_id, limit=limit)
    return {
        "trace_id": trace_id,
        "thread_id": thread_id,
        "trace_ids": sorted({event["trace_id"] for event in events}),
        "events": events,
        "mcp_boundary": _mcp_boundary(),
    }


@mcp.resource(
    "dataops://datasets",
    name="dataops_dataset_catalog",
    description="Supported datasets, active versions, and governance boundary.",
    mime_type="application/json",
)
def dataset_catalog_resource() -> str:
    """Expose the governed dataset catalog as a read-only MCP resource."""

    return _json_resource(list_datasets())


@mcp.resource(
    "dataops://datasets/{dataset_name}/contract",
    name="dataops_dataset_contract_resource",
    description="Dataset contract template by dataset name or alias.",
    mime_type="application/json",
)
def dataset_contract_resource(dataset_name: str) -> str:
    """Expose a dataset contract as a read-only MCP resource template."""

    return _json_resource(readonly_dataset_contract(dataset_name))


@mcp.resource(
    "dataops://governance/boundary",
    name="dataops_mcp_governance_boundary",
    description="MCP control-plane allowlist and forbidden operations.",
    mime_type="application/json",
)
def governance_boundary_resource() -> str:
    """Expose the fixed MCP write boundary for Agent clients."""

    return _json_resource(
        {
            "surface": "mcp",
            "purpose": "read, preview, and create approval-pending governance tasks",
            "boundary": _mcp_boundary(),
            "forbidden_operations": [
                "approve",
                "publish",
                "rollback",
                "execute_shell",
                "write_active_version",
            ],
        }
    )


@mcp.resource(
    "dataops://skills",
    name="dataops_skill_registry",
    description="Reusable project Skill registry and boundary self-check.",
    mime_type="application/json",
)
def skill_registry_resource() -> str:
    """Expose reusable DataOps Skill definitions without granting write permissions."""

    return _json_resource(skill_registry_snapshot())


@mcp.resource(
    "dataops://scorecard/latest",
    name="dataops_latest_scorecard",
    description="Latest local scorecard report when present.",
    mime_type="application/json",
)
def latest_scorecard_resource() -> str:
    """Expose the latest scorecard report as a read-only MCP resource."""

    scorecard_path = PROJECT_ROOT / "reports" / "dataops_scorecard.json"
    if not scorecard_path.exists():
        return _json_resource({"status": "missing", "path": "reports/dataops_scorecard.json"})
    return scorecard_path.read_text(encoding="utf-8")


@mcp.prompt(
    name="dataops_quality_triage",
    description="Help an Agent client triage a dataset before any governed write.",
)
def quality_triage_prompt(dataset_name: str = "service_requests") -> str:
    """Return a reusable quality-triage prompt template for Agent clients."""

    dataset = _dataset_key(dataset_name)
    return (
        "You are preparing a DataOps quality triage. "
        f"Use dataset `{dataset}`. First read the dataset contract, then profile and validate "
        "the candidate file, then create a repair preview if issues exist. "
        "Do not approve, publish, rollback, execute shell commands, or call live models unless "
        "the user explicitly approves that separate action."
    )


@mcp.prompt(
    name="dataops_release_risk_review",
    description="Review release risk before a human approval decision.",
)
def release_risk_review_prompt(dataset_name: str = "service_requests") -> str:
    """Return a reusable release-risk review prompt template for Agent clients."""

    dataset = _dataset_key(dataset_name)
    return (
        "Review this DataOps release candidate as a human-approval assistant. "
        f"Dataset: `{dataset}`. Explain source trust, schema fit, quality issues, repair preview, "
        "consumer regression impact, rollback point, and audit trace coverage. "
        "Treat the answer as advice only; approval and publish actions must stay outside MCP."
    )


def main() -> None:
    mcp.run(transport="stdio")


def _safe_project_path(path: str) -> Path:
    candidate = Path(path.replace("\\", "/"))
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    resolved = candidate.resolve()
    if PROJECT_ROOT not in (resolved, *resolved.parents):
        raise ValueError("Path must stay inside project root.")
    return resolved


def _dataset_key(dataset_name: str) -> str:
    dataset = DATASET_ALIASES.get(dataset_name, dataset_name)
    if dataset not in REQUIRED_COLUMNS_BY_DATASET:
        raise ValueError(f"Unsupported dataset_name: {dataset_name}")
    return dataset


def _dataset_contract(dataset_name: str) -> dict[str, Any]:
    return build_dataset_contract(_dataset_key(dataset_name))


def _mcp_boundary() -> dict[str, bool]:
    return {
        "can_profile": True,
        "can_validate": True,
        "can_preview_repair": True,
        "can_create_approval_pending_governance_run": True,
        "can_approve": False,
        "can_publish": False,
        "can_rollback": False,
        "can_execute_shell": False,
    }


def _json_resource(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
