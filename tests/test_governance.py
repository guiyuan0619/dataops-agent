from __future__ import annotations

from datetime import date

import pytest

from dataops_agent.agent_tools import ToolCall, validate_tool_calls
from dataops_agent.governance import resume_governance_run, start_governance_run, thread_status
from dataops_agent.rules import quality_report
from dataops_agent.versioning import VersionStore
from tests.test_rules_repair_versioning import _clean_df

pytestmark = pytest.mark.integration


def test_tool_allowlist_blocks_unknown_tool():
    with pytest.raises(ValueError):
        validate_tool_calls([ToolCall(name="execute_shell", arguments={"cmd": "dir"})])


def test_governance_run_interrupts_then_publishes_after_approval(
    dirty_doctor_governance_paths,
):
    paths = dirty_doctor_governance_paths
    first = start_governance_run(
        thread_id="thread-approved",
        dataset_name="doctors",
        dataset_path=paths.dirty,
        checkpoint_path=paths.checkpoint,
        version_db_path=paths.version_db,
        artifact_dir=paths.artifact_dir,
    )
    assert first["status"] == "awaiting_approval"
    assert len(first["interrupt"]["preview"]["changes"]) == 5
    assert paths.checkpoint.exists()

    saved = thread_status(thread_id="thread-approved", checkpoint_path=paths.checkpoint)
    assert saved["status"] == "awaiting_approval"

    final = resume_governance_run(
        thread_id="thread-approved",
        approved=True,
        approver="tester",
        checkpoint_path=paths.checkpoint,
    )
    assert final["status"] == "published"
    assert final["approver"] == "tester"
    assert final["quality_report"]["issue_count"] == 0
    assert final["version"]["version_id"].startswith("v-")

    store = VersionStore(paths.version_db, paths.artifact_dir)
    assert store.active_version("doctors").version_id == final["version"]["version_id"]


def test_governance_denial_does_not_publish(dirty_doctor_governance_paths):
    paths = dirty_doctor_governance_paths
    start_governance_run(
        thread_id="thread-denied",
        dataset_name="doctors",
        dataset_path=paths.dirty,
        checkpoint_path=paths.checkpoint,
        version_db_path=paths.version_db,
        artifact_dir=paths.artifact_dir,
    )
    final = resume_governance_run(
        thread_id="thread-denied",
        approved=False,
        approver="tester",
        checkpoint_path=paths.checkpoint,
    )
    assert final["status"] == "denied"
    assert VersionStore(paths.version_db, paths.artifact_dir).active_version("doctors") is None


def test_governance_blocks_publish_when_candidate_changes_after_approval(
    dirty_doctor_governance_paths,
):
    paths = dirty_doctor_governance_paths
    first = start_governance_run(
        thread_id="thread-mutated-candidate",
        dataset_name="doctors",
        dataset_path=paths.dirty,
        checkpoint_path=paths.checkpoint,
        version_db_path=paths.version_db,
        artifact_dir=paths.artifact_dir,
    )
    assert first["status"] == "awaiting_approval"

    paths.dirty.write_text(
        paths.dirty.read_text(encoding="utf-8").replace("Alice Chen", "Alice Changed"),
        encoding="utf-8",
    )

    final = resume_governance_run(
        thread_id="thread-mutated-candidate",
        approved=True,
        approver="tester",
        checkpoint_path=paths.checkpoint,
    )

    assert final["status"] == "candidate_changed_after_approval"
    assert final["expected_candidate_checksum"] != final["actual_candidate_checksum"]
    assert VersionStore(paths.version_db, paths.artifact_dir).active_version("doctors") is None


def test_governance_blocks_publish_when_candidate_drops_active_consumer_keys(tmp_path):
    version_db = tmp_path / "versions.sqlite3"
    artifact_dir = tmp_path / "versions"
    store = VersionStore(version_db, artifact_dir)
    baseline = _clean_df().iloc[:2].copy()
    active = store.publish(
        dataset_name="doctors",
        df=baseline,
        quality_report=quality_report(baseline, as_of=date(2026, 8, 21)),
        approved=True,
    )
    candidate = tmp_path / "incomplete.csv"
    baseline.iloc[:1].to_csv(candidate, index=False)

    result = start_governance_run(
        thread_id="thread-missing-baseline-key",
        dataset_name="doctors",
        dataset_path=candidate,
        checkpoint_path=tmp_path / "checkpoints.sqlite3",
        version_db_path=version_db,
        artifact_dir=artifact_dir,
    )

    assert result["status"] == "consumer_regression_failed"
    assert result["consumer_regression"]["metrics"]["missing_baseline_key_count"] == 1
    assert store.active_version("doctors").version_id == active.version_id
    assert len(store.list_versions("doctors")) == 1


def test_governance_resume_blocks_when_active_version_changed_after_preview(
    dirty_doctor_governance_paths,
):
    paths = dirty_doctor_governance_paths
    pending = start_governance_run(
        thread_id="thread-stale-active",
        dataset_name="doctors",
        dataset_path=paths.dirty,
        checkpoint_path=paths.checkpoint,
        version_db_path=paths.version_db,
        artifact_dir=paths.artifact_dir,
    )
    assert pending["status"] == "awaiting_approval"
    assert pending["interrupt"]["expected_active_version_id"] is None

    store = VersionStore(paths.version_db, paths.artifact_dir)
    baseline = _clean_df()
    newer = store.publish(
        dataset_name="doctors",
        df=baseline,
        quality_report=quality_report(baseline, as_of=date(2026, 8, 21)),
        approved=True,
    )

    final = resume_governance_run(
        thread_id="thread-stale-active",
        approved=True,
        approver="tester",
        checkpoint_path=paths.checkpoint,
    )

    assert final["status"] == "active_version_changed_before_publish"
    assert final["expected_active_version_id"] is None
    assert final["actual_active_version_id"] == newer.version_id
    assert store.active_version("doctors").version_id == newer.version_id
    assert len(store.list_versions("doctors")) == 1
