from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from dataops_agent.audit import IntegrationAuditStore
from dataops_agent.decision_memory import GovernanceDecisionMemoryStore
from dataops_agent.governance import (
    reconcile_governance_memory,
    resume_governance_run,
    start_governance_run,
    thread_status,
)
from dataops_agent.integration import (
    consumer_readiness,
    consumer_regression,
    consumer_rollback_drill,
    hospital_consumer_regression,
    hospital_readiness,
    hospital_rollback_drill,
    start_consumer_governance,
    start_hospital_governance,
)
from dataops_agent.repair import build_repair_plan, preview_plan
from dataops_agent.rules import detect_issues, load_csv, profile_dataframe, quality_report
from dataops_agent.versioning import VersionStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORE = VersionStore(
    PROJECT_ROOT / "data" / "dataops.sqlite3",
    PROJECT_ROOT / "data" / "versions",
)
DEFAULT_AUDIT_STORE = IntegrationAuditStore(PROJECT_ROOT / "data" / "dataops.sqlite3")
DEFAULT_DECISION_MEMORY_STORE = GovernanceDecisionMemoryStore(
    PROJECT_ROOT / "data" / "dataops.sqlite3"
)
DEFAULT_CHECKPOINT_PATH = PROJECT_ROOT / "data" / "agent_checkpoints.sqlite3"

app = FastAPI(title="DataOps Agent", version="0.1.0")


class DatasetPathRequest(BaseModel):
    path: str
    dataset_name: str = "doctors"


class RepairPreviewRequest(BaseModel):
    dataset_name: str
    path: str


class PublishRequest(BaseModel):
    dataset_name: str
    path: str
    approved: bool = False
    parent_version_id: str | None = None
    expected_active_version_id: str | None = None
    idempotency_key: str | None = None


class RollbackRequest(BaseModel):
    dataset_name: str
    target_version_id: str
    approved: bool = False
    approver: str = "local_user"


class GovernanceStartRequest(BaseModel):
    thread_id: str
    dataset_name: str
    path: str
    decision_memory_enabled: bool = True


class DecisionMappingReference(BaseModel):
    field: str
    source_value: str
    target_value: str


class GovernanceResumeRequest(BaseModel):
    thread_id: str
    approved: bool
    approver: str = "local_user"
    decision_reason: str = ""
    rejected_mappings: list[DecisionMappingReference] = Field(default_factory=list)


class DecisionMemoryTransitionRequest(BaseModel):
    actor: str
    reason: str


class DecisionMemoryContractInvalidationRequest(BaseModel):
    dataset_name: str
    contract_version: str
    actor: str
    reason: str


class HospitalReadinessRequest(BaseModel):
    dataset_name: str
    path: str
    consumer_agent: str = "hospital_agent"
    trace_id: str | None = None


class ConsumerReadinessRequest(BaseModel):
    dataset_name: str
    path: str
    consumer_agent: str = "generic_consumer"
    trace_id: str | None = None


class HospitalGovernanceStartRequest(BaseModel):
    thread_id: str
    dataset_name: str
    path: str
    consumer_agent: str = "hospital_agent"
    trace_id: str | None = None


class ConsumerGovernanceStartRequest(BaseModel):
    thread_id: str
    dataset_name: str
    path: str
    consumer_agent: str = "generic_consumer"
    trace_id: str | None = None


class HospitalRegressionRequest(BaseModel):
    dataset_name: str
    path: str
    consumer_agent: str = "hospital_agent"
    trace_id: str | None = None


class ConsumerRegressionRequest(BaseModel):
    dataset_name: str
    path: str
    consumer_agent: str = "generic_consumer"
    trace_id: str | None = None


class HospitalRollbackDrillRequest(BaseModel):
    dataset_name: str
    target_version_id: str
    approved: bool = False
    consumer_agent: str = "hospital_agent"
    trace_id: str | None = None


class ConsumerRollbackDrillRequest(BaseModel):
    dataset_name: str
    target_version_id: str
    approved: bool = False
    consumer_agent: str = "generic_consumer"
    trace_id: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/datasets/profile")
def profile_dataset(request: DatasetPathRequest) -> dict[str, Any]:
    df = load_csv(str(_safe_project_path(request.path)))
    return profile_dataframe(df, dataset_name=request.dataset_name).model_dump()


@app.post("/datasets/issues")
def dataset_issues(request: DatasetPathRequest) -> dict[str, Any]:
    df = load_csv(str(_safe_project_path(request.path)))
    return {
        "issues": [
            issue.model_dump() for issue in detect_issues(df, dataset_name=request.dataset_name)
        ]
    }


@app.post("/datasets/repair-preview")
def repair_preview(request: RepairPreviewRequest) -> dict[str, Any]:
    df = load_csv(str(_safe_project_path(request.path)))
    issues = detect_issues(df, dataset_name=request.dataset_name)
    plan = build_repair_plan(df, issues, dataset_name=request.dataset_name)
    return preview_plan(df, plan).model_dump()


@app.post("/versions/publish")
def publish_version(request: PublishRequest) -> dict[str, Any]:
    try:
        df = load_csv(str(_safe_project_path(request.path)))
        report = quality_report(df, dataset_name=request.dataset_name)
        record = DEFAULT_STORE.publish(
            dataset_name=request.dataset_name,
            df=df,
            quality_report=report,
            approved=request.approved,
            parent_version_id=request.parent_version_id,
            idempotency_key=request.idempotency_key,
            expected_active_version_id=(
                request.expected_active_version_id or request.parent_version_id
            ),
            require_expected_active_match=(
                request.expected_active_version_id is not None
                or request.parent_version_id is not None
            ),
        )
        return record.model_dump()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/versions/rollback")
def rollback_version(request: RollbackRequest) -> dict[str, Any]:
    try:
        record = DEFAULT_STORE.rollback(
            dataset_name=request.dataset_name,
            target_version_id=request.target_version_id,
            approved=request.approved,
            approver=request.approver,
        )
        return record.model_dump()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/versions/{dataset_name}/active")
def active_version(dataset_name: str) -> dict[str, Any]:
    record = DEFAULT_STORE.active_version(dataset_name)
    if not record:
        raise HTTPException(status_code=404, detail="No active version.")
    return record.model_dump()


@app.post("/agent/governance/start")
def start_governance(request: GovernanceStartRequest) -> dict[str, Any]:
    return start_governance_run(
        thread_id=request.thread_id,
        dataset_name=request.dataset_name,
        dataset_path=_safe_project_path(request.path),
        checkpoint_path=DEFAULT_CHECKPOINT_PATH,
        version_db_path=DEFAULT_STORE.db_path,
        artifact_dir=DEFAULT_STORE.artifact_dir,
        decision_memory_enabled=request.decision_memory_enabled,
    )


@app.post("/agent/governance/resume")
def resume_governance(request: GovernanceResumeRequest) -> dict[str, Any]:
    return resume_governance_run(
        thread_id=request.thread_id,
        approved=request.approved,
        approver=request.approver,
        decision_reason=request.decision_reason,
        rejected_mappings=[item.model_dump() for item in request.rejected_mappings],
        checkpoint_path=DEFAULT_CHECKPOINT_PATH,
    )


@app.get("/agent/governance/{thread_id}")
def get_governance_status(thread_id: str) -> dict[str, Any]:
    return thread_status(thread_id=thread_id, checkpoint_path=DEFAULT_CHECKPOINT_PATH)


@app.post("/agent/governance/{thread_id}/memory/reconcile")
def reconcile_governance_decision_memory(thread_id: str) -> dict[str, Any]:
    try:
        return reconcile_governance_memory(
            thread_id=thread_id, checkpoint_path=DEFAULT_CHECKPOINT_PATH
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/agent/memory/decisions")
def list_governance_decisions(
    dataset_name: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    try:
        decisions = DEFAULT_DECISION_MEMORY_STORE.list_decisions(
            dataset_name=dataset_name, status=status, limit=limit
        )
        return {"count": len(decisions), "decisions": decisions}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/agent/memory/events")
def list_governance_decision_events(
    decision_id: str | None = None, limit: int = 100
) -> dict[str, Any]:
    events = DEFAULT_DECISION_MEMORY_STORE.list_events(
        decision_id=decision_id, limit=limit
    )
    return {"count": len(events), "events": events}


@app.post("/agent/memory/decisions/{decision_id}/revoke")
def revoke_governance_decision(
    decision_id: str, request: DecisionMemoryTransitionRequest
) -> dict[str, Any]:
    try:
        return DEFAULT_DECISION_MEMORY_STORE.revoke(
            decision_id=decision_id, actor=request.actor, reason=request.reason
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/agent/memory/contracts/invalidate")
def invalidate_governance_decision_contract(
    request: DecisionMemoryContractInvalidationRequest,
) -> dict[str, Any]:
    try:
        count = DEFAULT_DECISION_MEMORY_STORE.invalidate_contract(
            dataset_name=request.dataset_name,
            contract_version=request.contract_version,
            actor=request.actor,
            reason=request.reason,
        )
        return {"status": "invalidated", "invalidated_count": count}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/integrations/consumers/readiness")
def consumer_agent_readiness(request: ConsumerReadinessRequest) -> dict[str, Any]:
    return consumer_readiness(
        dataset_name=request.dataset_name,
        dataset_path=_safe_project_path(request.path),
        store=DEFAULT_STORE,
        consumer_agent=request.consumer_agent,
        audit_store=DEFAULT_AUDIT_STORE,
        trace_id=request.trace_id,
    )


@app.post("/integrations/consumers/governance/start")
def consumer_agent_governance_start(request: ConsumerGovernanceStartRequest) -> dict[str, Any]:
    return start_consumer_governance(
        thread_id=request.thread_id,
        dataset_name=request.dataset_name,
        dataset_path=_safe_project_path(request.path),
        checkpoint_path=DEFAULT_CHECKPOINT_PATH,
        store=DEFAULT_STORE,
        consumer_agent=request.consumer_agent,
        audit_store=DEFAULT_AUDIT_STORE,
        trace_id=request.trace_id,
    )


@app.post("/integrations/consumers/regression")
def consumer_agent_regression(request: ConsumerRegressionRequest) -> dict[str, Any]:
    return consumer_regression(
        dataset_name=request.dataset_name,
        dataset_path=_safe_project_path(request.path),
        store=DEFAULT_STORE,
        consumer_agent=request.consumer_agent,
        audit_store=DEFAULT_AUDIT_STORE,
        trace_id=request.trace_id,
    )


@app.post("/integrations/consumers/rollback-drill")
def consumer_agent_rollback_drill(request: ConsumerRollbackDrillRequest) -> dict[str, Any]:
    if request.approved:
        raise HTTPException(
            status_code=403,
            detail="Consumer integration cannot approve rollback; use DataOps approval flow.",
        )
    return consumer_rollback_drill(
        dataset_name=request.dataset_name,
        target_version_id=request.target_version_id,
        store=DEFAULT_STORE,
        approved=request.approved,
        consumer_agent=request.consumer_agent,
        audit_store=DEFAULT_AUDIT_STORE,
        trace_id=request.trace_id,
    )


@app.get("/integrations/consumers/traces/{trace_id}")
def consumer_agent_trace(trace_id: str, limit: int = 50) -> dict[str, Any]:
    return _audit_events_response(trace_id=trace_id, limit=limit)


@app.get("/integrations/consumers/governance/{thread_id}/events")
def consumer_agent_governance_events(thread_id: str, limit: int = 50) -> dict[str, Any]:
    return _audit_events_response(thread_id=thread_id, limit=limit)


@app.post("/integrations/hospital/readiness")
def hospital_agent_readiness(request: HospitalReadinessRequest) -> dict[str, Any]:
    return hospital_readiness(
        dataset_name=request.dataset_name,
        dataset_path=_safe_project_path(request.path),
        store=DEFAULT_STORE,
        consumer_agent=request.consumer_agent,
        audit_store=DEFAULT_AUDIT_STORE,
        trace_id=request.trace_id,
    )


@app.post("/integrations/hospital/governance/start")
def hospital_agent_governance_start(request: HospitalGovernanceStartRequest) -> dict[str, Any]:
    return start_hospital_governance(
        thread_id=request.thread_id,
        dataset_name=request.dataset_name,
        dataset_path=_safe_project_path(request.path),
        checkpoint_path=DEFAULT_CHECKPOINT_PATH,
        store=DEFAULT_STORE,
        consumer_agent=request.consumer_agent,
        audit_store=DEFAULT_AUDIT_STORE,
        trace_id=request.trace_id,
    )


@app.post("/integrations/hospital/regression")
def hospital_agent_regression(request: HospitalRegressionRequest) -> dict[str, Any]:
    return hospital_consumer_regression(
        dataset_name=request.dataset_name,
        dataset_path=_safe_project_path(request.path),
        store=DEFAULT_STORE,
        consumer_agent=request.consumer_agent,
        audit_store=DEFAULT_AUDIT_STORE,
        trace_id=request.trace_id,
    )


@app.post("/integrations/hospital/rollback-drill")
def hospital_agent_rollback_drill(request: HospitalRollbackDrillRequest) -> dict[str, Any]:
    if request.approved:
        raise HTTPException(
            status_code=403,
            detail="Hospital integration cannot approve rollback; use DataOps approval flow.",
        )
    return hospital_rollback_drill(
        dataset_name=request.dataset_name,
        target_version_id=request.target_version_id,
        store=DEFAULT_STORE,
        approved=request.approved,
        consumer_agent=request.consumer_agent,
        audit_store=DEFAULT_AUDIT_STORE,
        trace_id=request.trace_id,
    )


@app.get("/integrations/hospital/traces/{trace_id}")
def hospital_agent_trace(trace_id: str, limit: int = 50) -> dict[str, Any]:
    return _audit_events_response(trace_id=trace_id, limit=limit)


@app.get("/integrations/hospital/governance/{thread_id}/events")
def hospital_agent_governance_events(thread_id: str, limit: int = 50) -> dict[str, Any]:
    return _audit_events_response(thread_id=thread_id, limit=limit)


def _audit_events_response(
    *,
    trace_id: str | None = None,
    thread_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    events = DEFAULT_AUDIT_STORE.list_events(trace_id=trace_id, thread_id=thread_id, limit=limit)
    return {
        "trace_id": trace_id,
        "thread_id": thread_id,
        "trace_ids": sorted({event["trace_id"] for event in events}),
        "events": events,
    }


def _safe_project_path(path: str) -> Path:
    candidate = Path(path.replace("\\", "/"))
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    resolved = candidate.resolve()
    if PROJECT_ROOT not in (resolved, *resolved.parents):
        raise HTTPException(status_code=400, detail="Path must stay inside project root.")
    return resolved
