from __future__ import annotations

from dataops_agent.approval_evidence import run_approval_status_evidence


def test_approval_status_evidence_covers_interview_states(tmp_path):
    report_json = tmp_path / "approval.json"
    report_md = tmp_path / "approval.md"

    report = run_approval_status_evidence(
        report_json=report_json,
        report_md=report_md,
        runtime_root=tmp_path / "runtime",
        run_id="test-approval-status",
    )

    assert report["status"] == "ok"
    assert report_json.exists()
    assert report_md.exists()
    assert report["scope"] == {
        "local_only": True,
        "ecs_used": False,
        "qwen_used": False,
        "production_traffic": False,
    }
    assert all(report["gates"].values())
    assert {item["name"] for item in report["status_cases"]} == {
        "待审批",
        "批准",
        "发布成功",
        "拒绝",
        "人工复核",
        "门禁失败",
    }
    statuses = {item["status"] for item in report["status_cases"]}
    assert {
        "awaiting_approval",
        "approved",
        "published",
        "denied",
        "manual_review_required",
        "consumer_regression_failed",
    } <= statuses
    assert "版本数量不变" in next(
        item for item in report["status_cases"] if item["name"] == "拒绝"
    )["write_effect"]
    assert "active_before=" in next(
        item for item in report["status_cases"] if item["name"] == "拒绝"
    )["evidence"]
    assert "missing_baseline_key_count=" in next(
        item for item in report["status_cases"] if item["name"] == "门禁失败"
    )["evidence"]
    assert "active_after=" in next(
        item for item in report["status_cases"] if item["name"] == "门禁失败"
    )["evidence"]
