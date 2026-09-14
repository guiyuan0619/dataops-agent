from __future__ import annotations

import sqlite3
from contextlib import closing

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from dataops_agent import api, decision_memory
from dataops_agent.decision_memory import GovernanceDecisionMemoryStore
from dataops_agent.governance import (
    reconcile_governance_memory,
    resume_governance_run,
    start_governance_run,
)
from dataops_agent.rules import quality_report
from dataops_agent.versioning import VersionStore


class CountingPlanner:
    def __init__(self, *proposals):
        self.proposals = list(proposals)
        self.calls = 0
        self.usage = {"total_tokens": 0}

    def propose(self, context):
        self.calls += 1
        self.usage["total_tokens"] += 40
        return self.proposals.pop(0)


class ForbiddenPlanner:
    usage = {"total_tokens": 0}

    def propose(self, context):
        raise AssertionError("approved decision memory should avoid this model call")


def _proposal(source="Heart clinic", target="Cardiology"):
    return {
        "decision": "propose",
        "mappings": [{
            "field": "department", "source_value": source, "target_value": target,
        }],
        "confidence": 0.95,
        "reason": "exact semantic equivalent",
    }


def _candidate(path, department="Heart clinic"):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{
        "doctor_id": "D001",
        "name": "Doctor 1",
        "department": department,
        "title": "Attending Physician",
        "status": "active",
        "updated_at": "2026-09-10",
        "source": "hospital_roster",
    }]).to_csv(path, index=False)


def _two_row_candidate(path, departments=("Heart clinic", "Heart clinic")):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, department in enumerate(departments, start=1):
        rows.append({
            "doctor_id": f"D{index:03d}",
            "name": f"Doctor {index}",
            "department": department,
            "title": "Attending Physician",
            "status": "active",
            "updated_at": "2026-09-10",
            "source": "hospital_roster",
        })
    pd.DataFrame(rows).to_csv(path, index=False)


def _paths(tmp_path):
    return {
        "checkpoint_path": tmp_path / "checkpoints.sqlite3",
        "version_db_path": tmp_path / "dataops.sqlite3",
        "artifact_dir": tmp_path / "versions",
    }


def _approve_once(tmp_path, *, thread_id="first-approved"):
    candidate = tmp_path / f"{thread_id}.csv"
    _candidate(candidate)
    paths = _paths(tmp_path)
    planner = CountingPlanner(_proposal())
    pending = start_governance_run(
        thread_id=thread_id,
        dataset_name="doctors",
        dataset_path=candidate,
        planner_provider=planner,
        **paths,
    )
    final = resume_governance_run(
        thread_id=thread_id,
        approved=True,
        approver="data_steward",
        checkpoint_path=paths["checkpoint_path"],
    )
    return candidate, paths, planner, pending, final


def test_published_human_decision_is_reused_without_model_or_gate_bypass(tmp_path):
    candidate, paths, planner, pending, final = _approve_once(tmp_path)

    assert pending["semantic_planning"]["model_calls"] == 1
    assert pending["semantic_planning"]["model_tokens"] == 40
    assert planner.calls == 1
    assert final["status"] == "published"
    assert final["decision_memory_write"]["status"] == "recorded"

    version_store = VersionStore(paths["version_db_path"], paths["artifact_dir"])
    version_count = len(version_store.list_versions("doctors"))
    repeated = start_governance_run(
        thread_id="memory-repeat",
        dataset_name="doctors",
        dataset_path=candidate,
        planner_provider=ForbiddenPlanner(),
        **paths,
    )

    assert repeated["status"] == "awaiting_approval"
    assert repeated["semantic_planning"]["status"] == "memory_reused"
    assert repeated["semantic_planning"]["model_calls"] == 0
    assert repeated["semantic_planning"]["model_tokens"] == 0
    assert repeated["decision_memory_recall"]["approved_count"] == 1
    assert len(version_store.list_versions("doctors")) == version_count


def test_explicit_rejection_becomes_negative_memory_and_blocks_same_target(tmp_path):
    candidate = tmp_path / "rejected.csv"
    _candidate(candidate)
    paths = _paths(tmp_path)
    pending = start_governance_run(
        thread_id="explicit-rejection",
        dataset_name="doctors",
        dataset_path=candidate,
        planner_provider=CountingPlanner(_proposal()),
        **paths,
    )
    assert pending["status"] == "awaiting_approval"

    denied = resume_governance_run(
        thread_id="explicit-rejection",
        approved=False,
        approver="data_steward",
        decision_reason="Heart clinic is ambiguous in this source system.",
        checkpoint_path=paths["checkpoint_path"],
    )
    assert denied["status"] == "denied"
    assert denied["decision_memory_write"]["status"] == "recorded"

    repeated = start_governance_run(
        thread_id="rejected-repeat",
        dataset_name="doctors",
        dataset_path=candidate,
        planner_provider=CountingPlanner(_proposal(), _proposal()),
        **paths,
    )
    assert repeated["status"] == "manual_review_required"
    assert repeated["semantic_planning"]["status"] == "fallback"
    assert repeated["decision_memory_recall"]["rejected_count"] == 1
    assert VersionStore(paths["version_db_path"], paths["artifact_dir"]).active_version(
        "doctors"
    ) is None


def test_plain_denial_does_not_guess_that_every_mapping_was_rejected(tmp_path):
    candidate = tmp_path / "plain-denial.csv"
    _candidate(candidate)
    paths = _paths(tmp_path)
    start_governance_run(
        thread_id="plain-denial",
        dataset_name="doctors",
        dataset_path=candidate,
        planner_provider=CountingPlanner(_proposal()),
        **paths,
    )
    denied = resume_governance_run(
        thread_id="plain-denial",
        approved=False,
        approver="data_steward",
        checkpoint_path=paths["checkpoint_path"],
    )

    assert denied["decision_memory_write"] == {
        "status": "not_explicit", "recorded_count": 0,
    }
    assert GovernanceDecisionMemoryStore(paths["version_db_path"]).list_decisions() == []


def test_multi_mapping_rejection_requires_explicit_mapping_scope(tmp_path):
    candidate = tmp_path / "multi-denial.csv"
    _two_row_candidate(candidate, ("Heart clinic", "Brain center"))
    paths = _paths(tmp_path)
    proposal = {
        "decision": "propose",
        "mappings": [
            {
                "field": "department",
                "source_value": "Heart clinic",
                "target_value": "Cardiology",
            },
            {
                "field": "department",
                "source_value": "Brain center",
                "target_value": "Neurology",
            },
        ],
        "confidence": 0.95,
        "reason": "two exact semantic equivalents",
    }
    start_governance_run(
        thread_id="multi-denial",
        dataset_name="doctors",
        dataset_path=candidate,
        planner_provider=CountingPlanner(proposal),
        **paths,
    )
    denied = resume_governance_run(
        thread_id="multi-denial",
        approved=False,
        approver="data_steward",
        decision_reason="One mapping is ambiguous.",
        checkpoint_path=paths["checkpoint_path"],
    )

    assert denied["status"] == "denied"
    assert denied["decision_memory_write"]["status"] == "ambiguous_scope"
    assert GovernanceDecisionMemoryStore(paths["version_db_path"]).list_decisions() == []


def test_revocation_contract_change_and_tampering_all_fail_closed(tmp_path, monkeypatch):
    _, paths, _, _, final = _approve_once(tmp_path)
    store = GovernanceDecisionMemoryStore(paths["version_db_path"])
    decision_id = final["decision_memory_write"]["decision_ids"][0]

    with closing(sqlite3.connect(paths["version_db_path"])) as conn:
        conn.execute(
            "update governance_decision_memory set target_value = ? where decision_id = ?",
            ("Neurology", decision_id),
        )
        conn.commit()
    poisoned = store.recall(
        dataset_name="doctors",
        contract_version="doctors_v1",
        groups=[("department", "Heart clinic")],
    )
    assert poisoned["approved"] == []
    assert poisoned["ignored"]["invalid_integrity"] == 1

    _, second_paths, _, _, second_final = _approve_once(
        tmp_path / "second", thread_id="second-approved"
    )
    second_store = GovernanceDecisionMemoryStore(second_paths["version_db_path"])
    second_id = second_final["decision_memory_write"]["decision_ids"][0]
    monkeypatch.setitem(decision_memory.DATASET_SCHEMA_VERSIONS, "doctors", "doctors_v2")
    changed_contract = second_store.recall(
        dataset_name="doctors",
        contract_version="doctors_v2",
        groups=[("department", "Heart clinic")],
    )
    assert changed_contract["approved"] == []

    monkeypatch.setitem(decision_memory.DATASET_SCHEMA_VERSIONS, "doctors", "doctors_v1")
    revoked = second_store.revoke(
        decision_id=second_id,
        actor="data_steward",
        reason="Source semantics changed after review.",
    )
    assert revoked["status"] == "revoked"
    assert second_store.recall(
        dataset_name="doctors",
        contract_version="doctors_v1",
        groups=[("department", "Heart clinic")],
    )["approved"] == []


def test_rollback_invalidates_decisions_from_the_rolled_back_version(tmp_path):
    paths = _paths(tmp_path)
    baseline_path = tmp_path / "baseline.csv"
    _candidate(baseline_path, "Cardiology")
    baseline = pd.read_csv(baseline_path)
    version_store = VersionStore(paths["version_db_path"], paths["artifact_dir"])
    baseline_version = version_store.publish(
        dataset_name="doctors",
        df=baseline,
        quality_report=quality_report(baseline, dataset_name="doctors"),
        approved=True,
    )
    candidate = tmp_path / "candidate.csv"
    _candidate(candidate)
    start_governance_run(
        thread_id="rollback-memory",
        dataset_name="doctors",
        dataset_path=candidate,
        planner_provider=CountingPlanner(_proposal()),
        **paths,
    )
    final = resume_governance_run(
        thread_id="rollback-memory",
        approved=True,
        approver="data_steward",
        checkpoint_path=paths["checkpoint_path"],
    )
    decision_id = final["decision_memory_write"]["decision_ids"][0]

    version_store.rollback(
        dataset_name="doctors",
        target_version_id=baseline_version.version_id,
        approved=True,
        approver="data_steward",
    )

    decision = GovernanceDecisionMemoryStore(paths["version_db_path"]).list_decisions()[0]
    assert decision["decision_id"] == decision_id
    assert decision["status"] == "invalidated"
    with pytest.raises(ValueError, match="remain active"):
        GovernanceDecisionMemoryStore(paths["version_db_path"]).record_approved_mappings(
            dataset_name="doctors",
            contract_version="doctors_v1",
            mappings=[_proposal()["mappings"][0]],
            thread_id="stale-memory-reconcile",
            approver="data_steward",
            plan_checksum="stale-plan",
            published_version_id=final["version"]["version_id"],
        )


def test_rollback_and_memory_invalidation_are_atomic(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    first_path = tmp_path / "first.csv"
    second_path = tmp_path / "second.csv"
    _candidate(first_path, "Cardiology")
    _candidate(second_path, "Neurology")
    store = VersionStore(paths["version_db_path"], paths["artifact_dir"])
    first_df = pd.read_csv(first_path)
    second_df = pd.read_csv(second_path)
    first = store.publish(
        dataset_name="doctors",
        df=first_df,
        quality_report=quality_report(first_df, dataset_name="doctors"),
        approved=True,
    )
    second = store.publish(
        dataset_name="doctors",
        df=second_df,
        quality_report=quality_report(second_df, dataset_name="doctors"),
        approved=True,
        parent_version_id=first.version_id,
    )

    def fail_invalidation(self, conn, **kwargs):
        raise sqlite3.OperationalError("injected invalidation failure")

    monkeypatch.setattr(
        GovernanceDecisionMemoryStore,
        "invalidate_published_version_in_transaction",
        fail_invalidation,
    )
    with pytest.raises(sqlite3.OperationalError):
        store.rollback(
            dataset_name="doctors",
            target_version_id=first.version_id,
            approved=True,
            approver="data_steward",
        )
    assert store.active_version("doctors").version_id == second.version_id


def test_memory_reuse_still_cannot_bypass_consumer_regression(tmp_path):
    paths = _paths(tmp_path)
    seed = tmp_path / "seed.csv"
    _two_row_candidate(seed)
    start_governance_run(
        thread_id="memory-gate-seed",
        dataset_name="doctors",
        dataset_path=seed,
        planner_provider=CountingPlanner(_proposal()),
        **paths,
    )
    resume_governance_run(
        thread_id="memory-gate-seed",
        approved=True,
        approver="data_steward",
        checkpoint_path=paths["checkpoint_path"],
    )
    version_store = VersionStore(paths["version_db_path"], paths["artifact_dir"])
    original_active = version_store.active_version("doctors")
    incomplete = tmp_path / "incomplete.csv"
    _candidate(incomplete)

    pending = start_governance_run(
        thread_id="memory-gate-repeat",
        dataset_name="doctors",
        dataset_path=incomplete,
        planner_provider=ForbiddenPlanner(),
        **paths,
    )
    final = resume_governance_run(
        thread_id="memory-gate-repeat",
        approved=True,
        approver="data_steward",
        checkpoint_path=paths["checkpoint_path"],
    )

    assert pending["semantic_planning"]["status"] == "memory_reused"
    assert final["status"] == "consumer_regression_failed"
    assert version_store.active_version("doctors").version_id == original_active.version_id
    assert len(version_store.list_versions("doctors")) == 1


def test_failed_post_publish_memory_write_is_visible_and_idempotently_reconciled(
    tmp_path, monkeypatch
):
    candidate = tmp_path / "reconcile.csv"
    _candidate(candidate)
    paths = _paths(tmp_path)
    start_governance_run(
        thread_id="memory-reconcile",
        dataset_name="doctors",
        dataset_path=candidate,
        planner_provider=CountingPlanner(_proposal()),
        **paths,
    )
    original = GovernanceDecisionMemoryStore.record_approved_mappings

    def fail_write(self, **kwargs):
        raise sqlite3.OperationalError("injected memory outage")

    monkeypatch.setattr(GovernanceDecisionMemoryStore, "record_approved_mappings", fail_write)
    final = resume_governance_run(
        thread_id="memory-reconcile",
        approved=True,
        approver="data_steward",
        checkpoint_path=paths["checkpoint_path"],
    )
    assert final["status"] == "published"
    assert final["decision_memory_write"]["status"] == "write_failed"
    assert final["decision_memory_write"]["reconcile_available"] is True

    monkeypatch.setattr(GovernanceDecisionMemoryStore, "record_approved_mappings", original)
    reconciled = reconcile_governance_memory(
        thread_id="memory-reconcile", checkpoint_path=paths["checkpoint_path"]
    )
    repeated = reconcile_governance_memory(
        thread_id="memory-reconcile", checkpoint_path=paths["checkpoint_path"]
    )
    assert reconciled["status"] == "recorded"
    assert repeated["decision_ids"] == reconciled["decision_ids"]
    assert len(GovernanceDecisionMemoryStore(paths["version_db_path"]).list_decisions()) == 1


def test_memory_control_api_lists_revokes_and_exposes_audit_events(tmp_path, monkeypatch):
    _, paths, _, _, final = _approve_once(tmp_path)
    store = GovernanceDecisionMemoryStore(paths["version_db_path"])
    decision_id = final["decision_memory_write"]["decision_ids"][0]
    monkeypatch.setattr(api, "DEFAULT_DECISION_MEMORY_STORE", store)
    client = TestClient(api.app)

    listed = client.get("/agent/memory/decisions", params={"dataset_name": "doctors"})
    revoked = client.post(
        f"/agent/memory/decisions/{decision_id}/revoke",
        json={"actor": "data_steward", "reason": "Mapping is no longer valid."},
    )
    events = client.get("/agent/memory/events", params={"decision_id": decision_id})

    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert events.status_code == 200
    assert {event["event_type"] for event in events.json()["events"]} == {
        "recorded_approved", "revoked",
    }


def test_memory_control_api_invalidates_a_contract_scope(tmp_path, monkeypatch):
    _, paths, _, _, _ = _approve_once(tmp_path)
    store = GovernanceDecisionMemoryStore(paths["version_db_path"])
    monkeypatch.setattr(api, "DEFAULT_DECISION_MEMORY_STORE", store)

    response = TestClient(api.app).post(
        "/agent/memory/contracts/invalidate",
        json={
            "dataset_name": "doctors",
            "contract_version": "doctors_v1",
            "actor": "data_steward",
            "reason": "Contract semantics changed.",
        },
    )

    assert response.status_code == 200
    assert response.json()["invalidated_count"] == 1
    assert store.list_decisions()[0]["status"] == "invalidated"
