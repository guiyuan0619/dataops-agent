from __future__ import annotations

from dataops_agent.ci_quality_matrix import run_ci_quality_matrix


def test_ci_quality_matrix_covers_all_test_files_and_ci_ready_gates(tmp_path):
    report_json = tmp_path / "ci_quality_matrix.json"
    report_md = tmp_path / "ci_quality_matrix.md"

    report = run_ci_quality_matrix(
        report_json=report_json,
        report_md=report_md,
        run_id="test-ci-quality-matrix",
    )

    assert report["status"] == "ok"
    assert report["scope"]["ci_workflow_configured"] is True
    assert report["scope"]["ci_provider_executed"] is False
    assert report["scope"]["qwen_called_this_run"] is False
    assert report["scope"]["ecs_used_this_run"] is False
    assert report["metrics"]["suite_count"] == 5
    assert report["metrics"]["test_file_count"] == 40
    assert report["metrics"]["assigned_test_file_count"] == 40
    assert report["gates"]["all_test_files_assigned"] is True
    assert report["gates"]["no_stale_matrix_files"] is True
    assert report["gates"]["no_duplicate_test_assignment"] is True
    assert report["gates"]["workflow_runs_ruff"] is True
    assert report["gates"]["workflow_runs_test_matrix"] is True
    assert report["gates"]["workflow_runs_full_coverage"] is True
    assert report["gates"]["workflow_runs_scorecard"] is True
    assert report["gates"]["workflow_runs_ci_matrix_report"] is True
    assert report["gates"]["workflow_covers_test_matrix"] is True
    assert report["matrix_drift"]["workflow_missing_matrix_files"] == []
    assert report_json.exists()
    assert "测试矩阵" in report_md.read_text(encoding="utf-8")
