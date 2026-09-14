from __future__ import annotations

from uuid import uuid4

import pytest

from dataops_agent.integration import consumer_regression
from dataops_agent.rules import load_csv, quality_report
from dataops_agent.versioning import VersionStore

pytestmark = pytest.mark.api


def test_generic_consumer_readiness_accepts_service_requests(api_client):
    trace_id = "test-consumer-readiness-trace"

    response = api_client.post(
        "/integrations/consumers/readiness",
        json={
            "dataset_name": "service_requests",
            "path": "data/imported/nyc_311_service_requests_sample.csv",
            "consumer_agent": "service_request_agent",
            "trace_id": trace_id,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["contract"] == "dataops_consumer_v1"
    assert body["trace_id"] == trace_id
    assert body["consumer_agent"] == "service_request_agent"
    assert body["safe_for_consumer"] is True
    assert "safe_for_hospital_agent" not in body
    assert body["decision"] == "publish_requires_approval"
    assert body["quality_report"]["issue_count"] == 0

    trace_response = api_client.get(f"/integrations/consumers/traces/{trace_id}")
    assert trace_response.status_code == 200
    assert any(event["operation"] == "readiness" for event in trace_response.json()["events"])


def test_generic_consumer_regression_blocks_dirty_service_requests(api_client):
    response = api_client.post(
        "/integrations/consumers/regression",
        json={
            "dataset_name": "service_requests",
            "path": "data/derived/nyc_311_service_requests_dirty_sample.csv",
            "consumer_agent": "service_request_agent",
            "trace_id": "test-consumer-regression-trace",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["contract"] == "dataops_consumer_v1"
    assert body["status"] == "blocked_quality_gate"
    assert body["release_gate_passed"] is False
    assert body["metrics"]["quality_issue_count"] == 5
    assert "hospital regression" not in body["recommended_next_step"]
    assert "hospital traffic" not in body["notes"][0]


def test_generic_consumer_governance_requires_manual_review_for_service_requests(api_client):
    thread_id = f"consumer-service-requests-dirty-{uuid4().hex}"
    trace_id = f"test-consumer-governance-trace-{uuid4().hex}"

    response = api_client.post(
        "/integrations/consumers/governance/start",
        json={
            "thread_id": thread_id,
            "dataset_name": "service_requests",
            "path": "data/derived/nyc_311_service_requests_dirty_sample.csv",
            "consumer_agent": "service_request_agent",
            "trace_id": trace_id,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["contract"] == "dataops_consumer_v1"
    assert body["consumer_agent"] == "service_request_agent"
    assert body["status"] == "manual_review_required"
    assert body["unresolved_issue_count"] == 4
    assert "version" not in body

    events_response = api_client.get(f"/integrations/consumers/governance/{thread_id}/events")
    assert events_response.status_code == 200
    events_body = events_response.json()
    assert events_body["thread_id"] == thread_id
    assert events_body["trace_ids"] == [trace_id]
    assert any(
        event["operation"] == "governance_start"
        and event["thread_id"] == thread_id
        and event["trace_id"] == trace_id
        for event in events_body["events"]
    )


def test_generic_consumer_api_cannot_approve_rollback(api_client):
    response = api_client.post(
        "/integrations/consumers/rollback-drill",
        json={
            "dataset_name": "service_requests",
            "target_version_id": "v-any",
            "approved": True,
            "consumer_agent": "service_request_agent",
            "trace_id": "test-consumer-approved-rollback-denied",
        },
    )

    assert response.status_code == 403
    assert "cannot approve rollback" in response.json()["detail"]


def test_generic_consumer_regression_requires_baseline_key_coverage(tmp_path):
    df = load_csv("data/imported/nyc_311_service_requests_sample.csv")
    store = VersionStore(tmp_path / "versions.sqlite3", tmp_path / "versions")
    store.publish(
        dataset_name="service_requests",
        df=df,
        quality_report=quality_report(df, dataset_name="service_requests"),
        approved=True,
    )

    candidate = tmp_path / "candidate.csv"
    df.to_csv(candidate, index=False)
    passed = consumer_regression(
        dataset_name="service_requests",
        dataset_path=candidate,
        store=store,
        consumer_agent="service_request_agent",
    )

    assert passed["contract"] == "dataops_consumer_v1"
    assert passed["status"] == "passed"
    assert passed["metrics"]["baseline_key_coverage"] == 1.0

    missing_baseline = tmp_path / "candidate_missing_key.csv"
    df.iloc[:-1].to_csv(missing_baseline, index=False)
    failed = consumer_regression(
        dataset_name="service_requests",
        dataset_path=missing_baseline,
        store=store,
        consumer_agent="service_request_agent",
    )

    assert failed["status"] == "failed_regression"
    assert failed["release_gate_passed"] is False
    assert failed["metrics"]["missing_baseline_key_count"] == 1
    assert failed["failed_cases"][0]["reason"] == "missing_from_candidate"
