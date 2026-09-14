from __future__ import annotations

from pathlib import Path
from typing import Any

from dataops_agent.audit import IntegrationAuditStore, build_integration_trace_id
from dataops_agent.consumer_gate import evaluate_consumer_gate
from dataops_agent.governance import start_governance_run
from dataops_agent.rules import detect_issues, load_csv, profile_dataframe, quality_report
from dataops_agent.versioning import VersionStore

GENERIC_CONTRACT_NAME = "dataops_consumer_v1"
CONTRACT_NAME = "hospital_dataops_v1"


def consumer_readiness(
    *,
    dataset_name: str,
    dataset_path: Path,
    store: VersionStore,
    consumer_agent: str = "generic_consumer",
    audit_store: IntegrationAuditStore | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    result = hospital_readiness(
        dataset_name=dataset_name,
        dataset_path=dataset_path,
        store=store,
        consumer_agent=consumer_agent,
        audit_store=audit_store,
        trace_id=trace_id,
        contract_name=GENERIC_CONTRACT_NAME,
    )
    result["safe_for_consumer"] = result.pop("safe_for_hospital_agent")
    return result


def hospital_readiness(
    *,
    dataset_name: str,
    dataset_path: Path,
    store: VersionStore,
    consumer_agent: str = "hospital_agent",
    audit_store: IntegrationAuditStore | None = None,
    trace_id: str | None = None,
    contract_name: str = CONTRACT_NAME,
) -> dict[str, Any]:
    trace_id = trace_id or build_integration_trace_id(
        "readiness",
        consumer_agent,
        dataset_name,
        dataset_path,
    )
    df = load_csv(str(dataset_path))
    report = quality_report(df, dataset_name=dataset_name)
    active = store.active_version(dataset_name)
    if report["passes_quality_gate"]:
        decision = "publish_requires_approval"
        next_step = "request human approval before publishing this candidate dataset"
    else:
        decision = "governance_required"
        next_step = "start governance flow and wait for approval before release"
    result = {
        "contract": contract_name,
        "trace_id": trace_id,
        "consumer_agent": consumer_agent,
        "dataset_name": dataset_name,
        "candidate_path": str(dataset_path),
        "safe_for_hospital_agent": bool(report["passes_quality_gate"]),
        "decision": decision,
        "recommended_next_step": next_step,
        "profile": profile_dataframe(df, dataset_name=dataset_name).model_dump(),
        "quality_report": report,
        "active_version": active.model_dump() if active else None,
    }
    if audit_store is not None:
        event = audit_store.record_event(
            trace_id=trace_id,
            operation="readiness",
            consumer_agent=consumer_agent,
            dataset_name=dataset_name,
            status=decision,
            payload={
                "candidate_path": str(dataset_path),
                "safe_for_hospital_agent": result["safe_for_hospital_agent"],
                "quality_issue_count": report["issue_count"],
                "active_version_id": active.version_id if active else None,
            },
        )
        result["audit_event_id"] = event["event_id"]
    return result


def start_consumer_governance(
    *,
    thread_id: str,
    dataset_name: str,
    dataset_path: Path,
    checkpoint_path: Path,
    store: VersionStore,
    consumer_agent: str = "generic_consumer",
    audit_store: IntegrationAuditStore | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    return start_hospital_governance(
        thread_id=thread_id,
        dataset_name=dataset_name,
        dataset_path=dataset_path,
        checkpoint_path=checkpoint_path,
        store=store,
        consumer_agent=consumer_agent,
        audit_store=audit_store,
        trace_id=trace_id,
        contract_name=GENERIC_CONTRACT_NAME,
    )


def start_hospital_governance(
    *,
    thread_id: str,
    dataset_name: str,
    dataset_path: Path,
    checkpoint_path: Path,
    store: VersionStore,
    consumer_agent: str = "hospital_agent",
    audit_store: IntegrationAuditStore | None = None,
    trace_id: str | None = None,
    contract_name: str = CONTRACT_NAME,
) -> dict[str, Any]:
    trace_id = trace_id or build_integration_trace_id(
        "governance_start",
        consumer_agent,
        dataset_name,
        dataset_path,
        thread_id,
    )
    df = load_csv(str(dataset_path))
    report = quality_report(df, dataset_name=dataset_name)
    issues = detect_issues(df, dataset_name=dataset_name)
    if not issues:
        result = {
            "contract": contract_name,
            "trace_id": trace_id,
            "consumer_agent": consumer_agent,
            "thread_id": thread_id,
            "dataset_name": dataset_name,
            "status": "no_governance_needed",
            "quality_report": report,
            "recommended_next_step": "request human approval before publishing this clean dataset",
        }
        if audit_store is not None:
            event = audit_store.record_event(
                trace_id=trace_id,
                operation="governance_start",
                consumer_agent=consumer_agent,
                dataset_name=dataset_name,
                thread_id=thread_id,
                status="no_governance_needed",
                payload={
                    "candidate_path": str(dataset_path),
                    "quality_issue_count": report["issue_count"],
                    "auto_publish_blocked": True,
                },
            )
            result["audit_event_id"] = event["event_id"]
        return result
    result = start_governance_run(
        thread_id=thread_id,
        dataset_name=dataset_name,
        dataset_path=dataset_path,
        checkpoint_path=checkpoint_path,
        version_db_path=store.db_path,
        artifact_dir=store.artifact_dir,
    )
    result = {
        "contract": contract_name,
        "trace_id": trace_id,
        "consumer_agent": consumer_agent,
        "thread_id": thread_id,
        **result,
    }
    if audit_store is not None:
        event = audit_store.record_event(
            trace_id=trace_id,
            operation="governance_start",
            consumer_agent=consumer_agent,
            dataset_name=dataset_name,
            thread_id=thread_id,
            status=str(result.get("status") or "unknown"),
            payload={
                "candidate_path": str(dataset_path),
                "quality_issue_count": report["issue_count"],
                "preview_change_count": len(
                    result.get("interrupt", {}).get("preview", {}).get("changes", [])
                ),
                "requires_human_approval": True,
            },
        )
        result["audit_event_id"] = event["event_id"]
    return result


def consumer_regression(
    *,
    dataset_name: str,
    dataset_path: Path,
    store: VersionStore,
    consumer_agent: str = "generic_consumer",
    audit_store: IntegrationAuditStore | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    result = hospital_consumer_regression(
        dataset_name=dataset_name,
        dataset_path=dataset_path,
        store=store,
        consumer_agent=consumer_agent,
        audit_store=audit_store,
        trace_id=trace_id,
        contract_name=GENERIC_CONTRACT_NAME,
    )
    result["recommended_next_step"] = result["recommended_next_step"].replace(
        "hospital regression",
        "consumer regression",
    )
    result["notes"][0] = (
        "This is a controlled pre-release consumer regression, not online production traffic."
    )
    return result


def hospital_consumer_regression(
    *,
    dataset_name: str,
    dataset_path: Path,
    store: VersionStore,
    consumer_agent: str = "hospital_agent",
    audit_store: IntegrationAuditStore | None = None,
    trace_id: str | None = None,
    contract_name: str = CONTRACT_NAME,
) -> dict[str, Any]:
    trace_id = trace_id or build_integration_trace_id(
        "regression",
        consumer_agent,
        dataset_name,
        dataset_path,
    )
    df = load_csv(str(dataset_path))
    report = quality_report(df, dataset_name=dataset_name)
    active = store.active_version(dataset_name)
    gate = evaluate_consumer_gate(
        dataset_name=dataset_name,
        candidate=df,
        quality_report=report,
        baseline=load_csv(str(active.path)) if active else None,
    )

    result = {
        "contract": contract_name,
        "trace_id": trace_id,
        "consumer_agent": consumer_agent,
        "dataset_name": dataset_name,
        "candidate_path": str(dataset_path),
        "status": gate["status"],
        "release_gate_passed": gate["release_gate_passed"],
        "recommended_next_step": gate["recommended_next_step"],
        "quality_report": report,
        "active_version": active.model_dump() if active else None,
        "baseline_available": active is not None,
        "metrics": gate["metrics"],
        "failed_cases": gate["failed_cases"],
        "changed_cases": gate["changed_cases"],
        "notes": [
            "This is a controlled pre-release consumer regression, not online hospital traffic.",
            (
                "It checks candidate dataset quality and key consumer-visible coverage "
                "before human approval."
            ),
            "Publishing and rollback still require the approved DataOps version flow.",
        ],
    }
    if audit_store is not None:
        event = audit_store.record_event(
            trace_id=trace_id,
            operation="regression",
            consumer_agent=consumer_agent,
            dataset_name=dataset_name,
            status=gate["status"],
            payload={
                "candidate_path": str(dataset_path),
                "release_gate_passed": gate["release_gate_passed"],
                "active_version_id": active.version_id if active else None,
                "metrics": result["metrics"],
            },
        )
        result["audit_event_id"] = event["event_id"]
    return result


def consumer_rollback_drill(
    *,
    dataset_name: str,
    target_version_id: str,
    store: VersionStore,
    approved: bool = False,
    consumer_agent: str = "generic_consumer",
    audit_store: IntegrationAuditStore | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    result = hospital_rollback_drill(
        dataset_name=dataset_name,
        target_version_id=target_version_id,
        store=store,
        approved=approved,
        consumer_agent=consumer_agent,
        audit_store=audit_store,
        trace_id=trace_id,
        contract_name=GENERIC_CONTRACT_NAME,
    )
    result["recommended_next_step"] = result["recommended_next_step"].replace(
        "hospital regression",
        "consumer regression",
    )
    return result


def hospital_rollback_drill(
    *,
    dataset_name: str,
    target_version_id: str,
    store: VersionStore,
    approved: bool = False,
    consumer_agent: str = "hospital_agent",
    audit_store: IntegrationAuditStore | None = None,
    trace_id: str | None = None,
    contract_name: str = CONTRACT_NAME,
) -> dict[str, Any]:
    trace_id = trace_id or build_integration_trace_id(
        "rollback_drill",
        consumer_agent,
        dataset_name,
        target_version_id,
    )
    active_before = store.active_version(dataset_name)
    target = store.get_version(target_version_id)
    if target is None or target.dataset_name != dataset_name:
        status = "rollback_target_missing"
        rollback_executed = False
        active_after = active_before
        recommended_next_step = "choose a version that belongs to this dataset"
    elif not approved:
        status = "rollback_requires_approval"
        rollback_executed = False
        active_after = active_before
        recommended_next_step = "set approved=true only after human rollback approval"
    else:
        target = store.rollback(
            dataset_name=dataset_name,
            target_version_id=target_version_id,
            approved=True,
        )
        status = "rolled_back"
        rollback_executed = True
        active_after = target
        recommended_next_step = "run hospital regression against the restored active version"

    result = {
        "contract": contract_name,
        "trace_id": trace_id,
        "consumer_agent": consumer_agent,
        "dataset_name": dataset_name,
        "target_version_id": target_version_id,
        "status": status,
        "rollback_executed": rollback_executed,
        "release_gate_passed": rollback_executed,
        "recommended_next_step": recommended_next_step,
        "active_version_before": active_before.model_dump() if active_before else None,
        "active_version_after": active_after.model_dump() if active_after else None,
        "target_version": target.model_dump() if target else None,
    }
    if audit_store is not None:
        event = audit_store.record_event(
            trace_id=trace_id,
            operation="rollback_drill",
            consumer_agent=consumer_agent,
            dataset_name=dataset_name,
            status=status,
            payload={
                "target_version_id": target_version_id,
                "rollback_executed": rollback_executed,
                "active_before_id": active_before.version_id if active_before else None,
                "active_after_id": active_after.version_id if active_after else None,
            },
        )
        result["audit_event_id"] = event["event_id"]
    return result
