from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from dataops_agent.api import app


def test_hospital_readiness_reports_governance_required_for_dirty_data():
    client = TestClient(app)
    trace_id = "test-hospital-readiness-trace"

    response = client.post(
        "/integrations/hospital/readiness",
        json={
            "dataset_name": "doctors",
            "path": "data/derived/dirty_dev.csv",
            "consumer_agent": "hospital_agent",
            "trace_id": trace_id,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["contract"] == "hospital_dataops_v1"
    assert body["trace_id"] == trace_id
    assert body["safe_for_hospital_agent"] is False
    assert body["decision"] == "governance_required"
    assert body["quality_report"]["issue_count"] == 5

    trace_response = client.get(f"/integrations/hospital/traces/{trace_id}")
    assert trace_response.status_code == 200
    trace_body = trace_response.json()
    assert trace_body["trace_id"] == trace_id
    assert any(event["operation"] == "readiness" for event in trace_body["events"])


def test_hospital_governance_start_waits_for_approval_on_dirty_data():
    client = TestClient(app)
    thread_id = f"hospital-dirty-dev-{uuid4().hex}"
    trace_id = f"test-hospital-governance-trace-{uuid4().hex}"

    response = client.post(
        "/integrations/hospital/governance/start",
        json={
            "thread_id": thread_id,
            "dataset_name": "doctors",
            "path": "data/derived/dirty_dev.csv",
            "consumer_agent": "hospital_agent",
            "trace_id": trace_id,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["contract"] == "hospital_dataops_v1"
    assert body["consumer_agent"] == "hospital_agent"
    assert body["status"] == "awaiting_approval"
    assert len(body["interrupt"]["preview"]["changes"]) == 5

    events_response = client.get(f"/integrations/hospital/governance/{thread_id}/events")
    assert events_response.status_code == 200
    events_body = events_response.json()
    assert events_body["thread_id"] == thread_id
    assert events_body["trace_ids"] == [trace_id]
    assert events_body["events"][0]["operation"] == "governance_start"


def test_hospital_governance_start_does_not_auto_publish_clean_data():
    client = TestClient(app)

    response = client.post(
        "/integrations/hospital/governance/start",
        json={
            "thread_id": "hospital-clean-dev",
            "dataset_name": "doctors",
            "path": "data/raw/clean_dev.csv",
            "consumer_agent": "hospital_agent",
            "trace_id": "test-hospital-clean-trace",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "no_governance_needed"
    assert body["quality_report"]["issue_count"] == 0
    assert "version" not in body


def test_hospital_regression_blocks_dirty_data_before_release():
    client = TestClient(app)

    response = client.post(
        "/integrations/hospital/regression",
        json={
            "dataset_name": "doctors",
            "path": "data/derived/dirty_dev.csv",
            "consumer_agent": "hospital_agent",
            "trace_id": "test-hospital-regression-trace",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["contract"] == "hospital_dataops_v1"
    assert body["status"] == "blocked_quality_gate"
    assert body["release_gate_passed"] is False
    assert body["metrics"]["quality_issue_count"] == 5


def test_hospital_rollback_drill_reports_missing_target_without_switching_version():
    client = TestClient(app)

    response = client.post(
        "/integrations/hospital/rollback-drill",
        json={
            "dataset_name": "doctors",
            "target_version_id": "v-not-found",
            "approved": False,
            "consumer_agent": "hospital_agent",
            "trace_id": "test-hospital-rollback-trace",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["contract"] == "hospital_dataops_v1"
    assert body["status"] == "rollback_target_missing"
    assert body["rollback_executed"] is False


def test_hospital_integration_api_cannot_approve_rollback():
    client = TestClient(app)

    response = client.post(
        "/integrations/hospital/rollback-drill",
        json={
            "dataset_name": "doctors",
            "target_version_id": "v-any",
            "approved": True,
            "consumer_agent": "hospital_agent",
            "trace_id": "test-hospital-approved-rollback-denied",
        },
    )

    assert response.status_code == 403
    assert "cannot approve rollback" in response.json()["detail"]
