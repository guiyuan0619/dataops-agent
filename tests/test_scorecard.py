from __future__ import annotations

from dataops_agent.scorecard import run_scorecard


def test_scorecard_summarizes_local_evidence_without_live_calls(tmp_path):
    report_json = tmp_path / "scorecard.json"
    report_md = tmp_path / "scorecard.md"

    report = run_scorecard(
        report_json=report_json,
        report_md=report_md,
        runtime_root=tmp_path / "runtime",
        run_id="test-scorecard",
    )

    metrics = report["metrics"]

    assert report["status"] == "ok"
    assert report["scope"]["qwen_called_this_run"] is False
    assert report["scope"]["ecs_used_this_run"] is False
    assert metrics["data_sources"]["public_source_count"] == 2
    assert metrics["data_sources"]["authorized_private_source_count"] == 1
    assert metrics["governance_release"]["acceptance_case_count"] == 7
    assert metrics["governance_release"]["acceptance_pass_rate"] == 1.0
    assert metrics["approval_status"]["status_case_count"] == 6
    assert metrics["approval_status"]["all_status_gates_passed"] is True
    assert "门禁失败" in metrics["approval_status"]["status_cases"]
    assert metrics["freshness"]["source_count"] == 2
    assert metrics["freshness"]["dry_run_task_count"] == 2
    assert metrics["freshness"]["dry_run_only"] is True
    assert metrics["rolling_open_data"]["snapshot_count"] == 4
    assert metrics["rolling_open_data"]["new_record_count"] == 2
    assert metrics["rolling_open_data"]["late_arrival_count"] == 1
    assert metrics["rolling_open_data"]["bad_candidate_blocked_count"] == 2
    assert metrics["rolling_open_data"]["approved_rollback_restored_baseline"] is True
    assert metrics["rolling_open_data"]["production_traffic"] is False
    assert metrics["collaboration"]["scenario_count"] == 7
    assert metrics["collaboration"]["covered_scenario_count"] == 7
    assert metrics["collaboration"]["consumer_approved_rollback_blocked_at_api"] is True
    assert metrics["collaboration"]["a2a_protocol_implemented"] is False
    assert metrics["cross_project_recovery"]["scenario_count"] == 6
    assert metrics["cross_project_recovery"]["passed_scenario_count"] == 6
    assert metrics["cross_project_recovery"]["bad_candidate_blocked"] is True
    assert metrics["cross_project_recovery"]["restored_regression_passed"] is True
    assert (
        metrics["cross_project_recovery"]["hospital_production_system_connected"] is False
    )
    assert metrics["hospital_agent_impact"]["knowledge_doc_count"] == 5
    assert metrics["hospital_agent_impact"]["passed_canary_case_count"] == 6
    assert metrics["hospital_agent_impact"]["forbidden_doc_leak_count"] == 0
    assert metrics["hospital_agent_impact"]["direct_answer_accuracy_measured"] is False
    assert metrics["release_review"]["recommendation"] == "ready_for_human_approval"
    assert metrics["release_review"]["check_count"] == 5
    assert metrics["release_review"]["passed_check_count"] == 5
    assert metrics["release_review"]["reviewer_can_publish_active_version"] is False
    assert metrics["training_candidates"]["candidate_record_count"] == 7
    assert metrics["training_candidates"]["direct_sft_candidate_count"] == 0
    assert metrics["training_candidates"]["model_training_executed"] is False
    assert metrics["security"]["security_case_count"] == 7
    assert metrics["security"]["security_pass_rate"] == 1.0
    assert metrics["boundary_guard"]["boundary_case_count"] == 10
    assert metrics["boundary_guard"]["boundary_pass_rate"] == 1.0
    assert metrics["boundary_guard"]["rejected_change_count"] == 8
    assert metrics["boundary_guard"]["protected_project_count"] == 2
    assert metrics["ci_quality"]["suite_count"] == 5
    assert metrics["ci_quality"]["test_file_count"] == 40
    assert metrics["ci_quality"]["assigned_test_file_count"] == 40
    assert metrics["decision_memory"]["scenario_passed"] == 12
    assert metrics["decision_memory"]["scenario_count"] == 12
    assert metrics["decision_memory"]["model_call_reduction_percent"] == 100.0
    assert metrics["decision_memory"]["gate_bypass_count"] == 0
    assert metrics["ci_quality"]["workflow_file_present"] is True
    assert metrics["ci_quality"]["workflow_runs_scorecard"] is True
    assert metrics["ci_quality"]["ci_provider_executed"] is False
    assert metrics["downstream_consumption"]["candidate_case_count"] == 500
    assert metrics["performance"]["local_row_count"] == 500
    assert metrics["performance"]["multi_scale"]["status"] == "ok"
    assert metrics["performance"]["multi_scale"]["max_row_count"] == 5000
    assert metrics["performance"]["multi_scale"]["max_quality_issue_rate"] == 0
    assert metrics["version_consistency"]["status"] == "ok"
    assert metrics["version_consistency"]["duplicate_request_reuses_version"] is True
    assert (
        metrics["version_consistency"]["idempotency_key_reuse_with_different_data_blocked"] is True
    )
    assert metrics["version_consistency"]["stale_expected_active_version_blocked"] is True
    assert metrics["version_consistency"]["concurrent_publish_has_single_winner"] is True
    assert metrics["version_consistency"]["distributed_lock_used"] is False
    assert metrics["model_and_cloud"]["qwen_live"]["called_this_run"] is False
    assert metrics["model_and_cloud"]["ecs_current_validation"]["report_found"] is True
    assert metrics["model_and_cloud"]["ecs_current_validation"]["pytest_tests"] == 108
    assert metrics["model_and_cloud"]["ecs_current_validation"]["health_loop_all_ok"] is True
    assert report_json.exists()
    assert "不能说" in report_md.read_text(encoding="utf-8")
