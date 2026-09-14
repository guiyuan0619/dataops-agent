from __future__ import annotations

from dataops_agent.scenario_playbook import CORE_MVP_NODES, run_scenario_playbook


def test_scenario_playbook_covers_open_scenarios_without_new_runtime_dependency(tmp_path):
    report_json = tmp_path / "scenario_playbook.json"
    report_md = tmp_path / "scenario_playbook.md"

    report = run_scenario_playbook(
        report_json=report_json,
        report_md=report_md,
        run_id="test-scenario-playbook",
    )

    metrics = report["metrics"]
    scenarios = {scenario["scenario_id"]: scenario for scenario in report["scenarios"]}

    assert report["status"] == "ok"
    assert report["scope"]["ecs_used_this_run"] is False
    assert report["scope"]["qwen_called_this_run"] is False
    assert metrics["scenario_count"] == 4
    assert metrics["scenario_with_complete_mvp_count"] == 4
    assert metrics["core_mvp_node_count"] == len(CORE_MVP_NODES) == 8
    assert metrics["total_risk_count"] == 12
    assert metrics["new_dependency_required"] is False
    assert metrics["new_framework_required"] is False
    assert scenarios["enterprise_data_analytics"]["mvp_complete"] is True
    assert scenarios["customer_service_knowledge"]["mvp_complete"] is True
    assert scenarios["product_catalog_governance"]["mvp_complete"] is True
    assert scenarios["content_ops_governance"]["mvp_complete"] is True
    assert report_json.exists()
    assert "不能说" in report_md.read_text(encoding="utf-8")
