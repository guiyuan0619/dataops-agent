from __future__ import annotations

import pandas as pd

from dataops_agent.audit import IntegrationAuditStore
from dataops_agent.integration import hospital_readiness, hospital_rollback_drill
from dataops_agent.versioning import VersionStore


def test_hospital_readiness_records_trace_event(tmp_path):
    candidate = tmp_path / "doctors.csv"
    _doctor_df().to_csv(candidate, index=False)
    store = VersionStore(tmp_path / "versions.sqlite3", tmp_path / "versions")
    audit = IntegrationAuditStore(tmp_path / "audit.sqlite3")

    result = hospital_readiness(
        dataset_name="doctors",
        dataset_path=candidate,
        store=store,
        audit_store=audit,
        trace_id="trace-readiness-1",
    )

    events = audit.list_events(trace_id="trace-readiness-1")
    assert result["trace_id"] == "trace-readiness-1"
    assert result["audit_event_id"] == events[0]["event_id"]
    assert events[0]["operation"] == "readiness"
    assert events[0]["status"] == "publish_requires_approval"
    assert events[0]["payload"]["quality_issue_count"] == 0


def test_hospital_rollback_drill_requires_approval_before_switching_active_version(tmp_path):
    store = VersionStore(tmp_path / "versions.sqlite3", tmp_path / "versions")
    audit = IntegrationAuditStore(tmp_path / "audit.sqlite3")
    first = store.publish(
        dataset_name="doctors",
        df=_doctor_df(title="主任医师"),
        quality_report={"passes_quality_gate": True, "issue_count": 0},
        approved=True,
    )
    second = store.publish(
        dataset_name="doctors",
        df=_doctor_df(title="副主任医师"),
        quality_report={"passes_quality_gate": True, "issue_count": 0},
        approved=True,
        parent_version_id=first.version_id,
    )

    dry_run = hospital_rollback_drill(
        dataset_name="doctors",
        target_version_id=first.version_id,
        store=store,
        audit_store=audit,
        trace_id="trace-rollback-1",
    )

    assert dry_run["status"] == "rollback_requires_approval"
    assert dry_run["rollback_executed"] is False
    assert store.active_version("doctors").version_id == second.version_id

    approved = hospital_rollback_drill(
        dataset_name="doctors",
        target_version_id=first.version_id,
        store=store,
        approved=True,
        audit_store=audit,
        trace_id="trace-rollback-1",
    )

    events = audit.list_events(trace_id="trace-rollback-1")
    assert approved["status"] == "rolled_back"
    assert approved["rollback_executed"] is True
    assert store.active_version("doctors").version_id == first.version_id
    assert [event["status"] for event in events] == ["rollback_requires_approval", "rolled_back"]


def _doctor_df(*, title: str = "主任医师") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "doctor_id": "DOC-001",
                "name": "张医生",
                "department": "Cardiology",
                "title": title,
                "status": "active",
                "updated_at": "2026-08-01",
                "source": "official",
            }
        ]
    )
