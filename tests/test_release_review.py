from __future__ import annotations

from dataops_agent.release_review import run_release_review_report


def test_release_review_is_readonly_and_recommends_human_approval(tmp_path):
    report_json = tmp_path / "release_review.json"
    report_md = tmp_path / "release_review.md"

    report = run_release_review_report(
        report_json=report_json,
        report_md=report_md,
        runtime_root=tmp_path / "runtime",
        run_id="test-release-review",
    )

    metrics = report["metrics"]
    permissions = report["reviewer"]["permissions"]
    checks = {check["id"]: check for check in report["checks"]}

    assert report["status"] == "ok"
    assert metrics["recommendation"] == "ready_for_human_approval"
    assert metrics["check_count"] == 5
    assert metrics["passed_check_count"] == 5
    assert metrics["blocking_check_count"] == 0
    assert metrics["state_graph_node_count"] == 6
    assert permissions["can_approve_publish"] is False
    assert permissions["can_publish_active_version"] is False
    assert permissions["can_rollback_active_version"] is False
    assert permissions["can_execute_shell"] is False
    assert permissions["can_write_dataset"] is False
    assert checks["reviewer_permission_boundary_pass"]["status"] == "pass"
    assert report["scope"]["qwen_called_this_run"] is False
    assert report["scope"]["ecs_used_this_run"] is False
    assert report_json.exists()
    assert "不能说" in report_md.read_text(encoding="utf-8")
