from __future__ import annotations

import json

from dataops_agent.training_candidates import run_training_candidate_export


def test_training_candidate_export_marks_badcases_without_fake_sft(tmp_path):
    report_json = tmp_path / "training_candidates.json"
    report_md = tmp_path / "training_candidates.md"
    candidates_jsonl = tmp_path / "training_candidates.jsonl"

    report = run_training_candidate_export(
        report_json=report_json,
        report_md=report_md,
        candidates_jsonl=candidates_jsonl,
        runtime_root=tmp_path / "runtime",
        run_id="test-training-candidates",
    )

    records = [
        json.loads(line)
        for line in candidates_jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert report["status"] == "ok"
    assert report_json.exists()
    assert report_md.exists()
    assert len(records) == 7
    assert report["metrics"]["candidate_record_count"] == 7
    assert report["metrics"]["direct_sft_candidate_count"] == 0
    assert report["metrics"]["preference_candidate_count"] == 1
    assert report["metrics"]["deterministic_fix_first_count"] == 7
    assert report["scope"]["model_training_executed"] is False
    assert report["scope"]["qwen_called_this_run"] is False
    assert report["scope"]["ecs_used_this_run"] is False
    assert all(record["not_for_direct_training"] for record in records)
    assert all(record["split"] == "candidate_pool" for record in records)
    assert {record["source_case"] for record in records} == {
        "source_manifest_traceable",
        "unapproved_publish_blocked",
        "dirty_candidate_blocks_consumer_regression",
        "missing_baseline_key_blocks_release",
        "unresolved_public_data_issues_require_manual_review",
        "candidate_checksum_mismatch_blocks_publish",
        "unapproved_rollback_does_not_switch_active_version",
    }
    assert "不能说：已完成 SFT" in report_md.read_text(encoding="utf-8")
