from __future__ import annotations

from dataops_agent.concurrency import run_concurrency_report


def test_concurrency_report_covers_idempotency_and_active_conflicts(tmp_path):
    report_json = tmp_path / "concurrency.json"
    report_md = tmp_path / "concurrency.md"

    report = run_concurrency_report(
        report_json=report_json,
        report_md=report_md,
        runtime_root=tmp_path / "runtime",
        run_id="test-concurrency",
    )

    assert report["status"] == "ok"
    assert report["scope"]["local_only"] is True
    assert report["scope"]["distributed_lock_used"] is False
    assert report["gates"]["duplicate_request_reuses_version"] is True
    assert report["gates"]["idempotency_key_reuse_with_different_data_blocked"] is True
    assert report["gates"]["stale_expected_active_version_blocked"] is True
    assert report["summary"]["concurrent_published_count"] == 1
    assert report["summary"]["concurrent_conflict_count"] == 1
    assert report["cases"]["concurrent_publish_same_parent"]["version_count"] == 2
    assert report_json.exists()
    assert "分布式锁" in report_md.read_text(encoding="utf-8")


def test_concurrency_report_rerun_resets_runtime(tmp_path):
    report_json = tmp_path / "concurrency.json"
    report_md = tmp_path / "concurrency.md"
    runtime_root = tmp_path / "runtime"

    first = run_concurrency_report(
        report_json=report_json,
        report_md=report_md,
        runtime_root=runtime_root,
        run_id="repeatable",
    )
    second = run_concurrency_report(
        report_json=report_json,
        report_md=report_md,
        runtime_root=runtime_root,
        run_id="repeatable",
    )

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert second["summary"]["idempotent_version_count"] == 2
    assert second["summary"]["concurrent_published_count"] == 1
    assert second["summary"]["concurrent_conflict_count"] == 1
    assert second["cases"]["concurrent_publish_same_parent"]["version_count"] == 2
