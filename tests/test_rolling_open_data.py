from __future__ import annotations

from dataops_agent.rolling_open_data import run_rolling_open_data_report


def test_rolling_open_data_report_blocks_bad_updates_and_rolls_back(tmp_path):
    report = run_rolling_open_data_report(
        report_json=tmp_path / "rolling_open_data.json",
        report_md=tmp_path / "rolling_open_data.md",
        runtime_root=tmp_path / "runtime",
        run_id="test-rolling-open-data",
    )

    assert report["status"] == "ok"
    assert report["scope"]["network_called"] is False
    assert report["scope"]["qwen_called"] is False
    assert report["scope"]["ecs_used"] is False
    assert report["metrics"]["source_sample_row_count"] == 500
    assert report["metrics"]["snapshot_count"] == 4
    assert report["metrics"]["new_record_count"] == 2
    assert report["metrics"]["late_arrival_count"] == 1
    assert report["metrics"]["duplicate_key_count"] == 1
    assert report["metrics"]["schema_missing_required_column_count"] == 1
    assert report["metrics"]["bad_candidate_blocked_count"] == 2
    assert report["gates"]["duplicate_candidate_blocked"] is True
    assert report["gates"]["schema_break_blocked"] is True
    assert report["gates"]["active_preserved_after_bad_candidates"] is True
    assert report["gates"]["approved_rollback_restored_baseline"] is True
    assert "滚动开放数据" in (tmp_path / "rolling_open_data.md").read_text(
        encoding="utf-8"
    )
