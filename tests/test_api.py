from __future__ import annotations

from fastapi.testclient import TestClient

from dataops_agent import api
from dataops_agent.api import app
from dataops_agent.rules import quality_report
from dataops_agent.versioning import VersionStore
from tests.test_rules_repair_versioning import _clean_df


def test_health_endpoint():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_profile_endpoint_uses_project_relative_path():
    client = TestClient(app)
    response = client.post("/datasets/profile", json={"path": "data/raw/clean_dev.csv"})
    assert response.status_code == 200
    body = response.json()
    assert body["row_count"] == 6
    assert body["duplicate_key_count"] == 0


def test_unapproved_publish_is_blocked():
    client = TestClient(app)
    response = client.post(
        "/versions/publish",
        json={"dataset_name": "doctors", "path": "data/raw/clean_dev.csv", "approved": False},
    )
    assert response.status_code == 403


def test_publish_returns_conflict_when_consumer_gate_fails(tmp_path, monkeypatch):
    store = VersionStore(tmp_path / "versions.sqlite3", tmp_path / "versions")
    baseline = _clean_df().iloc[:2].copy()
    store.publish(
        dataset_name="doctors",
        df=baseline,
        quality_report=quality_report(baseline),
        approved=True,
    )
    candidate = tmp_path / "incomplete.csv"
    baseline.iloc[:1].to_csv(candidate, index=False)
    monkeypatch.setattr(api, "DEFAULT_STORE", store)
    monkeypatch.setattr(api, "_safe_project_path", lambda _: candidate)

    response = TestClient(app).post(
        "/versions/publish",
        json={"dataset_name": "doctors", "path": "ignored.csv", "approved": True},
    )

    assert response.status_code == 409
    assert "Consumer regression gate failed" in response.json()["detail"]
    assert len(store.list_versions("doctors")) == 1


def test_publish_api_reuses_idempotent_request(tmp_path, monkeypatch):
    store = VersionStore(tmp_path / "versions.sqlite3", tmp_path / "versions")
    candidate = tmp_path / "candidate.csv"
    _clean_df().to_csv(candidate, index=False)
    monkeypatch.setattr(api, "DEFAULT_STORE", store)
    monkeypatch.setattr(api, "_safe_project_path", lambda _: candidate)
    payload = {
        "dataset_name": "doctors",
        "path": "ignored.csv",
        "approved": True,
        "idempotency_key": "api-publish-request",
    }

    client = TestClient(app)
    first = client.post("/versions/publish", json=payload)
    repeated = client.post("/versions/publish", json=payload)

    assert first.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json()["version_id"] == first.json()["version_id"]
    assert len(store.list_versions("doctors")) == 1


def test_publish_api_returns_conflict_for_stale_expected_active(tmp_path, monkeypatch):
    store = VersionStore(tmp_path / "versions.sqlite3", tmp_path / "versions")
    baseline = _clean_df().iloc[:2].copy()
    active = store.publish(
        dataset_name="doctors",
        df=baseline,
        quality_report=quality_report(baseline),
        approved=True,
    )
    newer = baseline.copy()
    newer.at[0, "title"] = "Newer Active"
    store.publish(
        dataset_name="doctors",
        df=newer,
        quality_report=quality_report(newer),
        approved=True,
        expected_active_version_id=active.version_id,
        require_expected_active_match=True,
    )
    candidate = tmp_path / "candidate.csv"
    changed = baseline.copy()
    changed.at[1, "title"] = "Stale Candidate"
    changed.to_csv(candidate, index=False)
    monkeypatch.setattr(api, "DEFAULT_STORE", store)
    monkeypatch.setattr(api, "_safe_project_path", lambda _: candidate)

    response = TestClient(app).post(
        "/versions/publish",
        json={
            "dataset_name": "doctors",
            "path": "ignored.csv",
            "approved": True,
            "expected_active_version_id": active.version_id,
        },
    )

    assert response.status_code == 409
    assert "Active version changed before publish" in response.json()["detail"]
