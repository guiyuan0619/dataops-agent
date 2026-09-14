from __future__ import annotations

import tomllib
from pathlib import Path

from dataops_agent.boundary_guard import run_boundary_guard


def test_boundary_guard_reviews_scope_changes(tmp_path):
    report_json = tmp_path / "boundary.json"
    report_md = tmp_path / "boundary.md"
    manifest_md = tmp_path / "manifest.md"

    report = run_boundary_guard(
        report_json=report_json,
        report_md=report_md,
        manifest_md=manifest_md,
        run_id="test-boundary",
    )

    cases = {case["name"]: case for case in report["cases"]}

    assert report["status"] == "ok"
    assert report_json.exists()
    assert report_md.exists()
    assert manifest_md.exists()
    assert report["scope"]["ecs_used"] is False
    assert report["scope"]["qwen_used"] is False
    assert report["scope"]["api_key_recorded"] is False
    assert report["summary"]["case_count"] == 10
    assert report["summary"]["passed_case_count"] == 10
    assert report["summary"]["allowed_change_count"] == 2
    assert report["summary"]["rejected_change_count"] == 8
    assert cases["public_manifest_update_allowed"]["decision"] == "allowed"
    assert cases["prompt_tries_to_bypass_approval"]["decision"] == "rejected"
    assert cases["mcp_publish_tool_escalation"]["decision"] == "rejected"
    assert cases["resume_overclaims_production_security"]["decision"] == "rejected"
    assert cases["hospital_medical_decision_overreach"]["decision"] == "rejected"
    assert "## 审查样例" in report_md.read_text(encoding="utf-8")
    assert "## 项目边界" in manifest_md.read_text(encoding="utf-8")


def test_boundary_guard_has_packaged_entrypoint():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert (
        config["project"]["scripts"]["dataops-agent-boundary-guard"]
        == "dataops_agent.boundary_guard:main"
    )
