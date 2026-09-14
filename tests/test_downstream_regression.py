from __future__ import annotations

import pandas as pd

from dataops_agent.downstream_regression import run_hospital_downstream_regression


def test_hospital_downstream_regression_hits_released_active_records(tmp_path):
    dataset = tmp_path / "doctors.csv"
    knowledge = tmp_path / "hospital_knowledge.jsonl"
    report_json = tmp_path / "regression.json"
    report_md = tmp_path / "regression.md"
    pd.DataFrame(
        [
            {
                "doctor_id": "DOC-001",
                "name": "Alice Chen",
                "department": "Cardiology",
                "title": "Chief Physician",
                "status": "active",
                "updated_at": "2026-08-01",
                "source": "official",
            },
            {
                "doctor_id": "DOC-002",
                "name": "Ben Wang",
                "department": "Neurology",
                "title": "Attending Physician",
                "status": "active",
                "updated_at": "2026-08-01",
                "source": "official",
            },
            {
                "doctor_id": "DOC-003",
                "name": "Frank Xu",
                "department": "Cardiology",
                "title": "Resident Physician",
                "status": "inactive",
                "updated_at": "2026-08-01",
                "source": "official",
            },
        ]
    ).to_csv(dataset, index=False)

    report = run_hospital_downstream_regression(
        dataset_path=dataset,
        version_id="test-version",
        knowledge_output=knowledge,
        report_json=report_json,
        report_md=report_md,
        seg_project_root=tmp_path / "missing_seg_project",
    )

    assert report["status"] == "passed"
    assert report["search_provider"] == "_LocalHospitalSearchService"
    assert report_json.exists()
    assert report_md.exists()
    assert report["knowledge_doc_count"] == 2
    assert report["metrics"]["positive_case_count"] == 2
    assert report["metrics"]["negative_case_count"] == 1
    assert report["metrics"]["top1_accuracy"] == 1.0
    assert report["metrics"]["hit_at_k"] == 1.0
    assert report["metrics"]["tool_match_rate"] == 1.0
    assert report["metrics"]["forbidden_doc_leak_count"] == 0
    assert "DATAOPS-DOCTOR-DOC-003" not in knowledge.read_text(encoding="utf-8")
