from __future__ import annotations

import pandas as pd

from dataops_agent.integration import hospital_consumer_regression
from dataops_agent.versioning import VersionStore


def test_hospital_consumer_regression_passes_clean_first_release(tmp_path):
    candidate = tmp_path / "doctors.csv"
    _doctor_df().to_csv(candidate, index=False)
    store = VersionStore(tmp_path / "versions.sqlite3", tmp_path / "versions")

    result = hospital_consumer_regression(
        dataset_name="doctors",
        dataset_path=candidate,
        store=store,
        consumer_agent="hospital_agent",
    )

    assert result["status"] == "passed"
    assert result["release_gate_passed"] is True
    assert result["baseline_available"] is False
    assert result["metrics"]["candidate_case_count"] == 2
    assert result["metrics"]["candidate_canary_pass_rate"] == 1.0


def test_hospital_consumer_regression_blocks_dirty_candidate(tmp_path):
    candidate = tmp_path / "dirty_doctors.csv"
    _doctor_df(name="").to_csv(candidate, index=False)
    store = VersionStore(tmp_path / "versions.sqlite3", tmp_path / "versions")

    result = hospital_consumer_regression(
        dataset_name="doctors",
        dataset_path=candidate,
        store=store,
        consumer_agent="hospital_agent",
    )

    assert result["status"] == "blocked_quality_gate"
    assert result["release_gate_passed"] is False
    assert result["metrics"]["quality_issue_count"] >= 1


def test_hospital_consumer_regression_detects_missing_active_baseline_key(tmp_path):
    active = _doctor_df()
    candidate = tmp_path / "candidate_doctors.csv"
    active.iloc[:1].to_csv(candidate, index=False)
    store = VersionStore(tmp_path / "versions.sqlite3", tmp_path / "versions")
    store.publish(
        dataset_name="doctors",
        df=active,
        quality_report={"passes_quality_gate": True, "issue_count": 0},
        approved=True,
    )

    result = hospital_consumer_regression(
        dataset_name="doctors",
        dataset_path=candidate,
        store=store,
        consumer_agent="hospital_agent",
    )

    assert result["status"] == "failed_regression"
    assert result["release_gate_passed"] is False
    assert result["metrics"]["baseline_case_count"] == 2
    assert result["metrics"]["missing_baseline_key_count"] == 1
    assert result["failed_cases"] == [{"case_key": "DOC-002", "reason": "missing_from_candidate"}]


def _doctor_df(*, name: str = "张医生") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "doctor_id": "DOC-001",
                "name": name,
                "department": "Cardiology",
                "title": "主任医师",
                "status": "active",
                "updated_at": "2026-08-01",
                "source": "official",
            },
            {
                "doctor_id": "DOC-002",
                "name": "李医生",
                "department": "Neurology",
                "title": "副主任医师",
                "status": "active",
                "updated_at": "2026-08-01",
                "source": "official",
            },
        ]
    )
