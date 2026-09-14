from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from dataops_agent.contracts import build_contract_report, write_contract_report


def test_contract_report_covers_datasets_and_quality_dimensions():
    report = build_contract_report()

    assert report["status"] == "ok"
    assert {item["dataset_name"] for item in report["contracts"]} == {
        "doctors",
        "hospitals",
        "service_requests",
    }
    assert report["manifest_validation"]["status"] == "ok"

    service_requests = _contract(report, "service_requests")
    assert service_requests["schema_version"] == "service_requests_v1"
    assert service_requests["key_column"] == "request_id"
    assert service_requests["consumer_contract"]["key_field"] == "request_id"
    assert service_requests["quality_dimension_coverage"]["completeness"]["status"] == "supported"
    assert service_requests["quality_dimension_coverage"]["timeliness"]["status"] == (
        "partially_supported"
    )
    assert "local freshness report" in service_requests["quality_dimension_coverage"][
        "timeliness"
    ]["implementation"]
    assert service_requests["quality_dimension_coverage"]["accuracy"]["status"] == "not_claimed"
    assert service_requests["source_assets"][0]["candidate_boundary"] == (
        "candidate_only_not_auto_published"
    )


def test_contract_report_can_write_json_and_markdown(tmp_path):
    result = write_contract_report(tmp_path / "contracts.json", tmp_path / "contracts.md")

    assert result["status"] == "ok"
    payload = json.loads((tmp_path / "contracts.json").read_text(encoding="utf-8"))
    markdown = (tmp_path / "contracts.md").read_text(encoding="utf-8")
    assert payload["dataset_count"] == 3
    assert "DataOps Dataset Contract Report" in markdown
    assert "service_requests_v1" in markdown


def test_contracts_has_packaged_entrypoint():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert config["project"]["scripts"]["dataops-agent-contracts"] == (
        "dataops_agent.contracts:main"
    )


def _contract(report: dict[str, Any], dataset_name: str) -> dict[str, Any]:
    return next(item for item in report["contracts"] if item["dataset_name"] == dataset_name)
