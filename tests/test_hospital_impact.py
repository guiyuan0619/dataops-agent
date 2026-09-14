from __future__ import annotations

from dataops_agent.collaboration import run_collaboration_report
from dataops_agent.downstream_regression import DEFAULT_REPORT_JSON as DOWNSTREAM_REPORT
from dataops_agent.hospital_impact import run_hospital_impact_report


def test_hospital_impact_report_summarizes_downstream_boundary(tmp_path):
    collaboration_json = tmp_path / "collaboration.json"
    collaboration_md = tmp_path / "collaboration.md"
    report_json = tmp_path / "hospital_impact.json"
    report_md = tmp_path / "hospital_impact.md"

    run_collaboration_report(
        report_json=collaboration_json,
        report_md=collaboration_md,
        run_id="test-collaboration",
    )
    report = run_hospital_impact_report(
        downstream_report=DOWNSTREAM_REPORT,
        collaboration_report=collaboration_json,
        report_json=report_json,
        report_md=report_md,
        seg_project_root=tmp_path / "missing_seg_project",
        run_id="test-hospital-impact",
    )

    assert report["status"] == "ok"
    assert report["metrics"]["knowledge_doc_count"] == 5
    assert report["metrics"]["passed_canary_case_count"] == 6
    assert report["metrics"]["canary_case_count"] == 6
    assert report["metrics"]["forbidden_doc_leak_count"] == 0
    assert report["metrics"]["downstream_can_approve_count"] == 0
    assert report["scope"]["direct_hospital_answer_accuracy_eval"] is False
    assert report_json.exists()
    assert "不是直接证明医院问答准确率提升" in report_md.read_text(encoding="utf-8")


def test_hospital_impact_has_packaged_entrypoint():
    import tomllib

    payload = tomllib.loads((DOWNSTREAM_REPORT.parents[1] / "pyproject.toml").read_text())
    scripts = payload["project"]["scripts"]
    assert scripts["dataops-agent-hospital-impact"] == "dataops_agent.hospital_impact:main"
