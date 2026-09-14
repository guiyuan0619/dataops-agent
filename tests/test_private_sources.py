from __future__ import annotations

import pandas as pd

from dataops_agent.private_sources import (
    DEFAULT_INPUT_CSV,
    run_private_source_report,
)


def test_private_source_report_normalizes_authorized_export(tmp_path):
    candidate_csv = tmp_path / "authorized_service_requests.csv"
    report_json = tmp_path / "private_source.json"
    report_md = tmp_path / "private_source.md"

    report = run_private_source_report(
        candidate_csv=candidate_csv,
        report_json=report_json,
        report_md=report_md,
    )

    candidate = pd.read_csv(candidate_csv, dtype=str, keep_default_na=False)

    assert report["status"] == "ok"
    assert report["scope"]["ecs_used"] is False
    assert report["scope"]["qwen_used"] is False
    assert report["source"]["source_type"] == "authorized_private_export_fixture"
    assert report["source"]["credential_policy"] == "credentials_not_persisted"
    assert report["credential_scan"]["credential_leak_detected"] is False
    assert report["quality_report"]["passes_quality_gate"] is True
    assert report["normalization"]["row_count"] == 5
    assert len(candidate) == 5
    assert set(candidate["status"]) == {"Open", "In Progress", "Pending", "Closed"}
    assert report_json.exists()
    assert "不能说" in report_md.read_text(encoding="utf-8")


def test_private_source_report_blocks_secret_columns(tmp_path):
    raw = pd.read_csv(DEFAULT_INPUT_CSV, dtype=str, keep_default_na=False)
    raw["api_key"] = "not-a-real-secret"
    input_csv = tmp_path / "leaky_private_export.csv"
    candidate_csv = tmp_path / "authorized_service_requests.csv"
    raw.to_csv(input_csv, index=False)

    report = run_private_source_report(
        input_csv=input_csv,
        candidate_csv=candidate_csv,
        report_json=tmp_path / "private_source.json",
        report_md=tmp_path / "private_source.md",
    )

    assert report["status"] == "failed_schema"
    assert report["schema_check"]["unexpected_secret_columns"] == ["api_key"]
    assert report["credential_scan"]["credential_leak_detected"] is True
    assert report["normalization"] is None
    assert not candidate_csv.exists()
