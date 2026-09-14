from __future__ import annotations

import pandas as pd

from dataops_agent.governance import resume_governance_run, start_governance_run
from dataops_agent.rules import quality_report
from dataops_agent.versioning import VersionStore


class ScriptedSemanticPlanner:
    def __init__(self, *proposals):
        self.proposals = list(proposals)
        self.contexts = []

    def propose(self, context):
        self.contexts.append(context)
        return self.proposals.pop(0)


def _proposal(*mappings, confidence=0.95, decision="propose"):
    return {
        "decision": decision,
        "mappings": list(mappings),
        "confidence": float(confidence),
        "reason": "map an exact semantic equivalent from the observed enum group",
    }


def _mapping(source, target, field="department"):
    return {"field": field, "source_value": source, "target_value": target}


def _candidate(path, *departments):
    rows = []
    for index, department in enumerate(departments, start=1):
        rows.append({
            "doctor_id": f"D{index:03d}", "name": f"Doctor {index}",
            "department": department, "title": "Attending Physician",
            "status": "active", "updated_at": "2026-09-10", "source": "hospital_roster",
        })
    pd.DataFrame(rows).to_csv(path, index=False)


def _paths(tmp_path):
    return {
        "checkpoint_path": tmp_path / "checkpoints.sqlite3",
        "version_db_path": tmp_path / "versions.sqlite3",
        "artifact_dir": tmp_path / "versions",
    }


def test_semantic_plan_enters_preview_then_requires_approval_before_publish(tmp_path):
    candidate = tmp_path / "candidate.csv"
    _candidate(candidate, "Heart clinic")
    planner = ScriptedSemanticPlanner(_proposal(_mapping("Heart clinic", "Cardiology")))
    paths = _paths(tmp_path)

    pending = start_governance_run(
        thread_id="semantic-approved", dataset_name="doctors", dataset_path=candidate,
        planner_provider=planner, **paths,
    )

    assert pending["status"] == "awaiting_approval"
    assert pending["semantic_planning"]["status"] == "proposed"
    assert pending["semantic_planning"]["external_write_authority"] is False
    assert pending["preview"]["changes"][0]["after"] == "Cardiology"
    store = VersionStore(paths["version_db_path"], paths["artifact_dir"])
    assert store.active_version("doctors") is None
    assert "Doctor 1" not in str(planner.contexts)

    final = resume_governance_run(
        thread_id="semantic-approved", approved=True, approver="tester",
        checkpoint_path=paths["checkpoint_path"],
    )

    assert final["status"] == "published"
    assert final["quality_report"]["issue_count"] == 0
    assert len(planner.contexts) == 1


def test_service_request_status_context_includes_business_meanings(tmp_path):
    candidate = tmp_path / "service_requests.csv"
    pd.DataFrame(
        [
            {
                "request_id": "R001",
                "created_at": "2026-09-12T08:00:00Z",
                "agency": "TEST",
                "complaint_type": "Street Condition",
                "status": "Work has begun",
                "borough": "MANHATTAN",
                "source_url": "https://example.test/r1",
                "source_checksum": "checksum-r1",
            }
        ]
    ).to_csv(candidate, index=False)
    planner = ScriptedSemanticPlanner(
        _proposal(_mapping("Work has begun", "Started", field="status"))
    )

    result = start_governance_run(
        thread_id="status-meaning-contract",
        dataset_name="service_requests",
        dataset_path=candidate,
        planner_provider=planner,
        **_paths(tmp_path),
    )

    meanings = planner.contexts[0]["groups"][0]["allowed_value_meanings"]
    assert "just begun" in meanings["Started"]
    assert "actively underway" in meanings["In Progress"]
    assert result["status"] == "awaiting_approval"


def test_incomplete_mapping_gets_preview_observation_and_one_replan(tmp_path):
    candidate = tmp_path / "candidate.csv"
    _candidate(candidate, "Heart clinic", "Brain center")
    planner = ScriptedSemanticPlanner(
        _proposal(_mapping("Heart clinic", "Cardiology")),
        _proposal(
            _mapping("Heart clinic", "Cardiology"),
            _mapping("Brain center", "Neurology"),
        ),
    )

    result = start_governance_run(
        thread_id="semantic-replan", dataset_name="doctors", dataset_path=candidate,
        planner_provider=planner, **_paths(tmp_path),
    )

    assert result["status"] == "awaiting_approval"
    assert result["semantic_planning"]["model_calls"] == 2
    assert result["semantic_planning"]["replan_count"] == 1
    assert planner.contexts[1]["observation"]["mapped_groups"] == [
        {"field": "department", "source_value": "Heart clinic"}
    ]
    assert planner.contexts[1]["observation"]["unmapped_groups"] == [
        {"field": "department", "source_value": "Brain center"}
    ]
    assert len(result["preview"]["changes"]) == 2


def test_invalid_model_mapping_fails_closed_without_publish(tmp_path):
    candidate = tmp_path / "candidate.csv"
    _candidate(candidate, "Heart clinic")
    invalid = _proposal(_mapping("Heart clinic", "Imaginary Department"))
    paths = _paths(tmp_path)

    result = start_governance_run(
        thread_id="semantic-invalid", dataset_name="doctors", dataset_path=candidate,
        planner_provider=ScriptedSemanticPlanner(invalid, invalid), **paths,
    )

    assert result["status"] == "manual_review_required"
    assert result["semantic_planning"]["status"] == "fallback"
    store = VersionStore(paths["version_db_path"], paths["artifact_dir"])
    assert store.active_version("doctors") is None


def test_candidate_mutation_during_model_call_invalidates_the_run(tmp_path):
    candidate = tmp_path / "candidate.csv"
    _candidate(candidate, "Heart clinic")

    class MutatingPlanner:
        def propose(self, context):
            candidate.write_text(candidate.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            return _proposal(_mapping("Heart clinic", "Cardiology"))

    try:
        start_governance_run(
            thread_id="semantic-mutated", dataset_name="doctors", dataset_path=candidate,
            planner_provider=MutatingPlanner(), **_paths(tmp_path),
        )
    except ValueError as exc:
        assert "changed during planning" in str(exc)
    else:
        raise AssertionError("candidate mutation must invalidate semantic planning")


def test_active_version_change_during_model_call_keeps_the_original_snapshot(tmp_path):
    candidate = tmp_path / "candidate.csv"
    baseline_path = tmp_path / "baseline.csv"
    _candidate(candidate, "Heart clinic")
    _candidate(baseline_path, "Cardiology")
    paths = _paths(tmp_path)
    store = VersionStore(paths["version_db_path"], paths["artifact_dir"])

    class PublishingPlanner:
        def propose(self, context):
            baseline = pd.read_csv(baseline_path)
            store.publish(
                dataset_name="doctors", df=baseline,
                quality_report=quality_report(baseline, dataset_name="doctors"),
                approved=True,
            )
            return _proposal(_mapping("Heart clinic", "Cardiology"))

    pending = start_governance_run(
        thread_id="semantic-active-race", dataset_name="doctors", dataset_path=candidate,
        planner_provider=PublishingPlanner(), **paths,
    )

    assert pending["status"] == "awaiting_approval"
    assert pending["expected_active_version_id"] is None
    final = resume_governance_run(
        thread_id="semantic-active-race", approved=True,
        checkpoint_path=paths["checkpoint_path"],
    )
    assert final["status"] == "active_version_changed_before_publish"
