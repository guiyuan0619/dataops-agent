from __future__ import annotations

from dataops_agent.collaboration import run_collaboration_report


def test_collaboration_report_documents_downstream_boundaries(tmp_path):
    report_json = tmp_path / "collaboration.json"
    report_md = tmp_path / "collaboration.md"

    report = run_collaboration_report(
        report_json=report_json,
        report_md=report_md,
        run_id="test-collaboration",
    )

    metrics = report["metrics"]
    participants = {participant["agent"]: participant for participant in report["participants"]}
    scenarios = {scenario["id"]: scenario for scenario in report["scenarios"]}

    assert report["status"] == "ok"
    assert metrics["participant_count"] == 3
    assert metrics["scenario_count"] == 7
    assert metrics["covered_scenario_count"] == 7
    assert metrics["consumer_route_count"] == 6
    assert metrics["hospital_compatibility_route_count"] == 6
    assert metrics["consumer_approved_rollback_blocked_at_api"] is True
    assert metrics["downstream_can_approve_count"] == 0
    assert metrics["a2a_protocol_implemented"] is False
    assert metrics["production_message_bus"] is False
    assert participants["hospital_agent"]["permissions"]["can_approve_publish"] is False
    assert (
        participants["generic_consumer_agent"]["permissions"]["can_rollback_active_version"]
        is False
    )
    assert scenarios["mcp_is_governance_control_plane_not_a2a"]["status"] == "boundary"
    assert report_json.exists()
    assert "不能说" in report_md.read_text(encoding="utf-8")
