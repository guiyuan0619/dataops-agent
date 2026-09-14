from __future__ import annotations

import pandas as pd

from dataops_agent.dirty_data import inject_dirty_hospital_data
from dataops_agent.evaluate import evaluate_detection
from dataops_agent.governance import start_governance_run
from dataops_agent.integration import hospital_readiness, start_hospital_governance
from dataops_agent.rules import detect_issues, load_csv, quality_report
from dataops_agent.sources import normalize_cms_hospitals
from dataops_agent.versioning import VersionStore


def test_normalize_cms_hospitals_maps_public_source_to_dataops_schema(tmp_path):
    raw = tmp_path / "cms_raw.csv"
    output = tmp_path / "cms_hospitals.csv"
    _cms_raw_df().to_csv(raw, index=False)

    result = normalize_cms_hospitals(raw, output)
    df = load_csv(str(output))
    report = quality_report(df, dataset_name="hospitals")

    assert result["row_count"] == 2
    assert result["columns"] == [
        "hospital_id",
        "name",
        "city",
        "state",
        "hospital_type",
        "emergency_services",
        "rating",
        "source_url",
        "source_checksum",
    ]
    assert df.iloc[0].to_dict()["hospital_id"] == "010001"
    assert report["passes_quality_gate"] is True


def test_cms_hospital_rules_detect_missing_duplicate_and_invalid_values():
    df = pd.DataFrame(
        [
            _cms_hospital_row(),
            {
                **_cms_hospital_row(),
                "hospital_id": "010001",
                "name": "",
                "emergency_services": "Maybe",
                "rating": "9",
            },
        ]
    )

    issues = detect_issues(df, dataset_name="hospitals")

    assert {(issue.issue_type.value, issue.row_index, issue.field) for issue in issues} == {
        ("duplicate_record", 1, "hospital_id"),
        ("invalid_enum", 1, "emergency_services"),
        ("invalid_enum", 1, "rating"),
        ("missing_value", 1, "name"),
    }


def test_hospital_integration_accepts_clean_cms_hospital_dataset(tmp_path):
    raw = tmp_path / "cms_raw.csv"
    output = tmp_path / "cms_hospitals.csv"
    _cms_raw_df().to_csv(raw, index=False)
    normalize_cms_hospitals(raw, output)
    store = VersionStore(tmp_path / "versions.sqlite3", tmp_path / "versions")

    readiness = hospital_readiness(
        dataset_name="hospitals",
        dataset_path=output,
        store=store,
        consumer_agent="hospital_agent",
    )
    governance = start_hospital_governance(
        thread_id="cms-hospitals-clean",
        dataset_name="hospitals",
        dataset_path=output,
        checkpoint_path=tmp_path / "checkpoints.sqlite3",
        store=store,
        consumer_agent="hospital_agent",
    )

    assert readiness["decision"] == "publish_requires_approval"
    assert readiness["safe_for_hospital_agent"] is True
    assert readiness["quality_report"]["dataset_name"] == "hospitals"
    assert governance["status"] == "no_governance_needed"
    assert "version" not in governance


def test_unrepairable_cms_hospital_issues_require_manual_review(tmp_path):
    dirty = tmp_path / "cms_hospitals_dirty.csv"
    pd.DataFrame([{**_cms_hospital_row(), "rating": "9"}]).to_csv(dirty, index=False)

    result = start_governance_run(
        thread_id="cms-hospitals-dirty",
        dataset_name="hospitals",
        dataset_path=dirty,
        checkpoint_path=tmp_path / "checkpoints.sqlite3",
        version_db_path=tmp_path / "versions.sqlite3",
        artifact_dir=tmp_path / "versions",
    )

    assert result["status"] == "manual_review_required"
    assert result["issues"][0]["field"] == "rating"
    assert "version" not in result
    assert (
        VersionStore(tmp_path / "versions.sqlite3", tmp_path / "versions").active_version(
            "hospitals"
        )
        is None
    )


def test_cms_hospital_dirty_injection_metrics_are_exact(tmp_path):
    clean = tmp_path / "cms_hospitals.csv"
    dirty = tmp_path / "cms_hospitals_dirty.csv"
    labels = tmp_path / "cms_hospitals_dirty_labels.json"
    pd.DataFrame(
        [
            {**_cms_hospital_row(), "hospital_id": "010001"},
            {**_cms_hospital_row(), "hospital_id": "010005"},
            {**_cms_hospital_row(), "hospital_id": "010006"},
            {**_cms_hospital_row(), "hospital_id": "010007"},
            {**_cms_hospital_row(), "hospital_id": "010011"},
        ]
    ).to_csv(clean, index=False)

    inject_dirty_hospital_data(clean, dirty, labels)
    metrics = evaluate_detection(dirty, labels)

    assert metrics.true_positive == 5
    assert metrics.false_positive == 0
    assert metrics.false_negative == 0
    assert metrics.f1 == 1.0


def test_partially_repairable_cms_hospital_issues_require_manual_review(tmp_path):
    clean = tmp_path / "cms_hospitals.csv"
    dirty = tmp_path / "cms_hospitals_dirty.csv"
    labels = tmp_path / "cms_hospitals_dirty_labels.json"
    version_db = tmp_path / "versions.sqlite3"
    artifact_dir = tmp_path / "versions"
    pd.DataFrame(
        [
            {**_cms_hospital_row(), "hospital_id": "010001"},
            {**_cms_hospital_row(), "hospital_id": "010005"},
            {**_cms_hospital_row(), "hospital_id": "010006"},
            {**_cms_hospital_row(), "hospital_id": "010007"},
            {**_cms_hospital_row(), "hospital_id": "010011"},
        ]
    ).to_csv(clean, index=False)
    inject_dirty_hospital_data(clean, dirty, labels)

    result = start_governance_run(
        thread_id="cms-hospitals-partial",
        dataset_name="hospitals",
        dataset_path=dirty,
        checkpoint_path=tmp_path / "checkpoints.sqlite3",
        version_db_path=version_db,
        artifact_dir=artifact_dir,
    )

    assert result["status"] == "manual_review_required"
    assert result["unresolved_issue_count"] == 4
    assert "interrupt" not in result
    assert "version" not in result
    assert VersionStore(version_db, artifact_dir).active_version("hospitals") is None


def _cms_raw_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "facility_id": "010001",
                "facility_name": "SOUTHEAST HEALTH MEDICAL CENTER",
                "citytown": "DOTHAN",
                "state": "AL",
                "hospital_type": "Acute Care Hospitals",
                "emergency_services": "Yes",
                "hospital_overall_rating": "4",
                "source_url": "https://data.cms.gov/provider-data/api/1/datastore/query/xubh-q36u/0",
                "source_checksum": "abc123",
            },
            {
                "facility_id": "010005",
                "facility_name": "MARSHALL MEDICAL CENTERS",
                "citytown": "BOAZ",
                "state": "AL",
                "hospital_type": "Acute Care Hospitals",
                "emergency_services": "Yes",
                "hospital_overall_rating": "Not Available",
                "source_url": "https://data.cms.gov/provider-data/api/1/datastore/query/xubh-q36u/0",
                "source_checksum": "abc123",
            },
        ]
    )


def _cms_hospital_row() -> dict[str, str]:
    return {
        "hospital_id": "010001",
        "name": "SOUTHEAST HEALTH MEDICAL CENTER",
        "city": "DOTHAN",
        "state": "AL",
        "hospital_type": "Acute Care Hospitals",
        "emergency_services": "Yes",
        "rating": "4",
        "source_url": "https://data.cms.gov/provider-data/api/1/datastore/query/xubh-q36u/0",
        "source_checksum": "abc123",
    }
