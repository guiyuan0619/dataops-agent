from __future__ import annotations

from dataops_agent.acceptance import run_acceptance


def test_acceptance_report_exercises_core_failure_gates(tmp_path):
    report_json = tmp_path / "acceptance.json"
    report_md = tmp_path / "acceptance.md"

    report = run_acceptance(
        report_json=report_json,
        report_md=report_md,
        runtime_root=tmp_path / "runtime",
        run_id="test-acceptance",
    )

    cases = {case["name"]: case for case in report["cases"]}
    catalog = {item["name"]: item for item in report["counterexample_catalog"]}

    assert report["status"] == "ok"
    assert report_json.exists()
    assert report_md.exists()
    assert report["scope"]["ecs_used"] is False
    assert report["scope"]["qwen_used"] is False
    assert report["summary"] == {
        "case_count": 7,
        "passed_case_count": 7,
        "failed_case_count": 0,
    }
    assert cases["source_manifest_traceable"]["passed"] is True
    assert cases["source_manifest_traceable"]["stage"] == "source_ingestion"
    assert cases["source_manifest_traceable"]["risk"] == "untraceable_public_source"
    assert cases["unapproved_publish_blocked"]["observed"] == (
        "Publishing requires explicit approval."
    )
    assert cases["dirty_candidate_blocks_consumer_regression"]["observed"] == (
        "blocked_quality_gate"
    )
    assert cases["missing_baseline_key_blocks_release"]["observed"] == "failed_regression"
    assert cases["unresolved_public_data_issues_require_manual_review"]["observed"] == (
        "manual_review_required"
    )
    assert cases["candidate_checksum_mismatch_blocks_publish"]["observed"] == (
        "candidate_changed_after_approval"
    )
    assert cases["unapproved_rollback_does_not_switch_active_version"]["observed"] == (
        "rollback_requires_approval"
    )
    assert set(catalog) == set(cases)
    assert catalog["missing_baseline_key_blocks_release"]["risk"] == (
        "lost_consumer_visible_record"
    )
    assert "## 反例库" in report_md.read_text(encoding="utf-8")
