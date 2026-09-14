from __future__ import annotations

import pandas as pd

from dataops_agent.dirty_data import inject_dirty_service_request_data
from dataops_agent.evaluate import evaluate_detection
from dataops_agent.rules import detect_issues, load_csv, quality_report
from dataops_agent.sources import normalize_nyc_311_service_requests
from dataops_agent.versioning import VersionStore


def test_normalize_nyc_311_service_requests_maps_public_source_to_schema(tmp_path):
    raw = tmp_path / "nyc_311_raw.csv"
    output = tmp_path / "service_requests.csv"
    _nyc_311_raw_df().to_csv(raw, index=False)

    result = normalize_nyc_311_service_requests(raw, output)
    df = load_csv(str(output))
    report = quality_report(df, dataset_name="service_requests")

    assert result["row_count"] == 2
    assert result["columns"] == [
        "request_id",
        "created_at",
        "agency",
        "complaint_type",
        "status",
        "borough",
        "source_url",
        "source_checksum",
    ]
    assert df.iloc[0].to_dict()["request_id"] == "70117611"
    assert report["passes_quality_gate"] is True


def test_service_request_rules_detect_missing_duplicate_and_invalid_values():
    df = pd.DataFrame(
        [
            _service_request_row(),
            {
                **_service_request_row(),
                "request_id": "REQ-001",
                "complaint_type": "",
                "status": "Maybe",
                "borough": "Atlantis",
            },
        ]
    )

    issues = detect_issues(df, dataset_name="service_requests")

    assert {(issue.issue_type.value, issue.row_index, issue.field) for issue in issues} == {
        ("duplicate_record", 1, "request_id"),
        ("invalid_enum", 1, "borough"),
        ("invalid_enum", 1, "status"),
        ("missing_value", 1, "complaint_type"),
    }


def test_service_request_dirty_injection_metrics_are_exact(tmp_path):
    clean = tmp_path / "service_requests.csv"
    dirty = tmp_path / "service_requests_dirty.csv"
    labels = tmp_path / "service_requests_dirty_labels.json"
    pd.DataFrame(
        [
            {**_service_request_row(), "request_id": "REQ-001"},
            {**_service_request_row(), "request_id": "REQ-002"},
            {**_service_request_row(), "request_id": "REQ-003"},
            {**_service_request_row(), "request_id": "REQ-004"},
            {**_service_request_row(), "request_id": "REQ-005"},
        ]
    ).to_csv(clean, index=False)

    inject_dirty_service_request_data(clean, dirty, labels)
    metrics = evaluate_detection(dirty, labels)

    assert metrics.true_positive == 5
    assert metrics.false_positive == 0
    assert metrics.false_negative == 0
    assert metrics.f1 == 1.0


def test_service_request_clean_dataset_can_be_versioned(tmp_path):
    df = pd.DataFrame([_service_request_row()])
    report = quality_report(df, dataset_name="service_requests")
    store = VersionStore(tmp_path / "versions.sqlite3", tmp_path / "versions")

    version = store.publish(
        dataset_name="service_requests",
        df=df,
        quality_report=report,
        approved=True,
    )

    assert report["passes_quality_gate"] is True
    assert store.active_version("service_requests").version_id == version.version_id


def _nyc_311_raw_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "unique_key": "70117611",
                "created_date": "2026-08-20T01:50:51.000",
                "agency": "NYPD",
                "complaint_type": "Blocked Driveway",
                "status": "In Progress",
                "borough": "QUEENS",
                "source_url": "https://data.cityofnewyork.us/resource/erm2-nwe9.json",
                "source_checksum": "abc123",
            },
            {
                "unique_key": "70115201",
                "created_date": "2026-08-20T01:50:46.000",
                "agency": "DEP",
                "complaint_type": "Water Maintenance",
                "status": "In Progress",
                "borough": "BRONX",
                "source_url": "https://data.cityofnewyork.us/resource/erm2-nwe9.json",
                "source_checksum": "abc123",
            },
        ]
    )


def _service_request_row() -> dict[str, str]:
    return {
        "request_id": "REQ-001",
        "created_at": "2026-08-20T01:50:51.000",
        "agency": "NYPD",
        "complaint_type": "Blocked Driveway",
        "status": "In Progress",
        "borough": "QUEENS",
        "source_url": "https://data.cityofnewyork.us/resource/erm2-nwe9.json",
        "source_checksum": "abc123",
    }
