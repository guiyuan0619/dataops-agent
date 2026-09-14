from __future__ import annotations

import tomllib
from pathlib import Path

from dataops_agent.cross_project_recovery import run_cross_project_recovery_drill


def test_cross_project_recovery_drill_blocks_bad_release_and_restores_active(tmp_path):
    report_json = tmp_path / "cross_project_recovery.json"
    report_md = tmp_path / "cross_project_recovery.md"

    report = run_cross_project_recovery_drill(
        report_json=report_json,
        report_md=report_md,
        runtime_root=tmp_path / "runtime",
        run_id="test-cross-project-recovery",
    )

    metrics = report["metrics"]

    assert report["status"] == "ok"
    assert report["scope"]["qwen_called_this_run"] is False
    assert report["scope"]["ecs_used_this_run"] is False
    assert report["scope"]["hospital_production_system_connected"] is False
    assert metrics["scenario_count"] == 6
    assert metrics["passed_scenario_count"] == 6
    assert metrics["bad_candidate_blocked"] is True
    assert metrics["active_preserved_after_bad_publish"] is True
    assert metrics["rollback_requires_dataops_approval"] is True
    assert metrics["approved_rollback_executed"] is True
    assert metrics["restored_active_matches_stable"] is True
    assert metrics["restored_regression_passed"] is True
    assert metrics["audit_event_count"] >= 5
    assert (
        report["versions"]["restored_active_version_id"]
        == report["versions"]["stable_version_id"]
    )
    assert (
        report["versions"]["active_after_bad_publish"]
        == report["versions"]["safe_published_version_id"]
    )
    assert report["consumer_results"]["bad_regression"]["missing_baseline_key_count"] == 1
    assert report_json.exists()
    assert "坏候选" in report_md.read_text(encoding="utf-8")


def test_cross_project_recovery_has_packaged_entrypoint():
    payload = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    scripts = payload["project"]["scripts"]
    assert (
        scripts["dataops-agent-cross-project-recovery"]
        == "dataops_agent.cross_project_recovery:main"
    )
