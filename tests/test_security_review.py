from __future__ import annotations

import tomllib
from pathlib import Path

from dataops_agent.security_review import run_security_review


def test_security_review_report_covers_adversarial_cases(tmp_path):
    report_json = tmp_path / "security.json"
    report_md = tmp_path / "security.md"

    report = run_security_review(
        report_json=report_json,
        report_md=report_md,
        runtime_root=tmp_path / "runtime",
        run_id="test-security",
    )

    cases = {case["name"]: case for case in report["cases"]}

    assert report["status"] == "ok"
    assert report_json.exists()
    assert report_md.exists()
    assert report["scope"]["ecs_used"] is False
    assert report["scope"]["qwen_used"] is False
    assert report["scope"]["api_key_recorded"] is False
    assert report["summary"] == {
        "case_count": 7,
        "passed_case_count": 7,
        "failed_case_count": 0,
    }
    assert cases["prompt_injection_text_cannot_publish_without_approval"]["passed"] is True
    assert cases["qwen_live_tool_surface_is_readonly"]["evidence"]["tool_names"] == [
        "build_repair_plan",
        "detect_issues",
    ]
    assert cases["unknown_shell_tool_is_rejected"]["passed"] is True
    assert cases["mcp_project_escape_path_is_rejected"]["observed"] == (
        "Path must stay inside project root."
    )
    assert cases["non_http_source_is_rejected"]["observed"] == (
        "Only http and https public sources are supported."
    )
    assert cases["rollback_requires_approval"]["passed"] is True
    assert cases["audit_query_limit_is_capped"]["evidence"]["returned_event_count"] == 200
    assert "## 对抗场景" in report_md.read_text(encoding="utf-8")


def test_security_review_has_packaged_entrypoint():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert (
        config["project"]["scripts"]["dataops-agent-security-review"]
        == "dataops_agent.security_review:main"
    )
