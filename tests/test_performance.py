from __future__ import annotations

from dataops_agent.performance import run_multiscale_performance_report, run_performance_report


def test_performance_report_covers_500_row_service_requests(tmp_path):
    report_json = tmp_path / "performance.json"
    report_md = tmp_path / "performance.md"

    report = run_performance_report(
        report_json=report_json,
        report_md=report_md,
        runtime_root=tmp_path / "runtime",
        run_id="test-performance",
    )

    assert report["status"] == "ok"
    assert report_json.exists()
    assert report_md.exists()
    assert report["scope"]["ecs_used"] is False
    assert report["scope"]["qwen_used"] is False
    assert report["dataset_scale"]["service_requests_row_count"] == 500
    assert report["gates"]["service_requests_regression_500_cases"] is True
    assert report["service_requests_regression"]["metrics"]["candidate_case_count"] == 500
    assert report["service_requests_regression"]["metrics"]["baseline_case_count"] == 500
    assert "不能说" in report_md.read_text(encoding="utf-8")


def test_multiscale_performance_report_records_scale_metrics(tmp_path):
    report_json = tmp_path / "multiscale-performance.json"
    report_md = tmp_path / "multiscale-performance.md"

    report = run_multiscale_performance_report(
        report_json=report_json,
        report_md=report_md,
        runtime_root=tmp_path / "runtime",
        run_id="test-multiscale-performance",
        scale_rows=(50, 100, 150),
    )

    assert report["status"] == "ok"
    assert report_json.exists()
    assert report_md.exists()
    assert report["scope"]["ecs_used"] is False
    assert report["scope"]["qwen_used"] is False
    assert report["scope"]["synthetic_scale_extension"] is True
    assert report["dataset_scale"]["scale_point_count"] == 3
    assert report["dataset_scale"]["max_row_count"] == 150
    assert report["summary"]["max_quality_issue_rate"] == 0
    assert report["summary"]["max_consumer_failed_case_rate"] == 0
    assert all(item["gates"]["consumer_regression_passed"] for item in report["scale_reports"])
    assert "多规模" in report_md.read_text(encoding="utf-8")
    assert "不能说" in report_md.read_text(encoding="utf-8")


def test_multiscale_performance_report_rerun_resets_runtime(tmp_path):
    report_json = tmp_path / "multiscale-performance.json"
    report_md = tmp_path / "multiscale-performance.md"
    runtime_root = tmp_path / "runtime"

    first = run_multiscale_performance_report(
        report_json=report_json,
        report_md=report_md,
        runtime_root=runtime_root,
        run_id="repeatable-multiscale",
        scale_rows=(50, 100, 150),
    )
    second = run_multiscale_performance_report(
        report_json=report_json,
        report_md=report_md,
        runtime_root=runtime_root,
        run_id="repeatable-multiscale",
        scale_rows=(50, 100, 150),
    )

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert second["dataset_scale"]["scale_point_count"] == 3
    assert second["summary"]["max_consumer_failed_case_rate"] == 0
    assert [item["previous_active_row_count"] for item in second["scale_reports"]] == [
        0,
        50,
        100,
    ]
    assert all(item["gates"]["consumer_regression_passed"] for item in second["scale_reports"])
