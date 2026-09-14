from __future__ import annotations

from dataops_agent.demo import run_local_demo


def test_local_demo_runs_resume_regression_rollback_and_trace(tmp_path):
    report_json = tmp_path / "demo.json"
    report_md = tmp_path / "demo.md"

    report = run_local_demo(
        report_json=report_json,
        report_md=report_md,
        runtime_root=tmp_path / "runtime",
        run_id="test-demo",
    )

    assert report["status"] == "ok"
    assert report_json.exists()
    assert report_md.exists()
    assert report["doctor_dataset"]["detection_metrics"]["f1"] == 1.0
    assert report["cms_public_dataset"]["detection_metrics"]["f1"] == 1.0
    assert report["service_request_dataset"]["detection_metrics"]["f1"] == 1.0
    assert report["service_request_dataset"]["quality_report"]["passes_quality_gate"] is True
    assert report["doctor_dataset"]["readiness_decision"] == "governance_required"
    assert report["doctor_dataset"]["governance_final_status"] == "published"
    assert report["doctor_dataset"]["consumer_regression_status"] == "passed"
    assert report["doctor_dataset"]["downstream_hospital_agent_status"] == "passed"
    assert report["doctor_dataset"]["downstream_hospital_agent_metrics"]["hit_at_k"] == 1.0
    assert report["doctor_dataset"]["rollback_dry_run_status"] == "rollback_requires_approval"
    assert report["doctor_dataset"]["rollback_final_status"] == "rolled_back"
    assert report["cms_public_dataset"]["dirty_governance_status"] == "manual_review_required"
    assert report["cms_public_dataset"]["consumer_regression_status"] == "passed"
    assert report["service_request_dataset"]["consumer_regression_status"] == "passed"
    assert report["gates"]["unapproved_publish_blocked"] is True
    assert report["gates"]["downstream_hospital_retrieval_passed"] is True
    assert report["gates"]["service_request_public_version_published"] is True
    assert report["gates"]["cross_domain_consumer_regression_passed"] is True
    assert report["cross_domain_consumer_regression"]["status"] == "passed"
    assert set(report["cross_domain_consumer_regression"]["results"]) == {
        "doctors",
        "hospitals",
        "service_requests",
    }
    assert report["gates"]["public_source_manifest_valid"] is True
    assert report["source_manifest"]["source_count"] == 2
    assert report["gates"]["trace_events_recorded"] is True
