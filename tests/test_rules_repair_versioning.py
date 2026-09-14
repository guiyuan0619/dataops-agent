from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import pandas as pd
import pytest

from dataops_agent.dirty_data import inject_dirty_doctor_data
from dataops_agent.evaluate import evaluate_detection
from dataops_agent.repair import apply_plan, build_repair_plan, preview_plan
from dataops_agent.rules import detect_issues, load_csv, quality_report
from dataops_agent.versioning import (
    ActiveVersionConflictError,
    IdempotencyConflictError,
    VersionStore,
)


def test_dirty_data_detection_metrics_are_exact(tmp_path):
    clean = tmp_path / "clean.csv"
    dirty = tmp_path / "dirty.csv"
    labels = tmp_path / "labels.json"
    _clean_df().to_csv(clean, index=False)

    inject_dirty_doctor_data(clean, dirty, labels)

    metrics = evaluate_detection(dirty, labels)
    assert metrics.true_positive == 5
    assert metrics.false_positive == 0
    assert metrics.false_negative == 0
    assert metrics.f1 == 1.0


def test_repair_preview_and_apply_plan_reduce_rule_issues(tmp_path):
    clean = tmp_path / "clean.csv"
    dirty = tmp_path / "dirty.csv"
    labels = tmp_path / "labels.json"
    _clean_df().to_csv(clean, index=False)
    inject_dirty_doctor_data(clean, dirty, labels)
    df = load_csv(str(dirty))

    issues = detect_issues(df, as_of=date(2026, 8, 21))
    plan = build_repair_plan(df, issues, dataset_name="doctors")
    preview = preview_plan(df, plan)
    repaired = apply_plan(df, plan)

    assert len(preview.changes) == 5
    assert len(repaired) == len(df) - 1
    assert quality_report(repaired, as_of=date(2026, 8, 21))["issue_count"] == 0


def test_version_publish_and_rollback_require_approval(tmp_path):
    store = VersionStore(tmp_path / "dataops.sqlite3", tmp_path / "versions")
    df = _clean_df()
    report = quality_report(df, as_of=date(2026, 8, 21))

    with pytest.raises(PermissionError):
        store.publish(dataset_name="doctors", df=df, quality_report=report, approved=False)

    first = store.publish(dataset_name="doctors", df=df, quality_report=report, approved=True)
    changed = df.copy()
    changed.at[0, "title"] = "Unknown"
    second = store.publish(
        dataset_name="doctors",
        df=changed,
        quality_report=quality_report(changed, as_of=date(2026, 8, 21)),
        approved=True,
        parent_version_id=first.version_id,
    )
    assert store.active_version("doctors").version_id == second.version_id

    with pytest.raises(PermissionError):
        store.rollback(dataset_name="doctors", target_version_id=first.version_id, approved=False)

    rolled_back = store.rollback(
        dataset_name="doctors",
        target_version_id=first.version_id,
        approved=True,
    )
    assert rolled_back.version_id == first.version_id
    assert store.active_version("doctors").version_id == first.version_id


def test_publish_blocks_candidate_that_drops_active_consumer_keys(tmp_path):
    store = VersionStore(tmp_path / "dataops.sqlite3", tmp_path / "versions")
    baseline = _clean_df()
    report = quality_report(baseline, as_of=date(2026, 8, 21))
    active = store.publish(
        dataset_name="doctors",
        df=baseline,
        quality_report=report,
        approved=True,
    )
    incomplete = baseline.iloc[:1].copy()

    with pytest.raises(ValueError, match="Consumer regression gate failed"):
        store.publish(
            dataset_name="doctors",
            df=incomplete,
            quality_report=quality_report(incomplete, as_of=date(2026, 8, 21)),
            approved=True,
        )

    assert store.active_version("doctors").version_id == active.version_id
    assert len(store.list_versions("doctors")) == 1


def test_publish_allows_explicit_doctor_inactivation_when_record_is_preserved(tmp_path):
    store = VersionStore(tmp_path / "dataops.sqlite3", tmp_path / "versions")
    baseline = _clean_df().iloc[:2].copy()
    first = store.publish(
        dataset_name="doctors",
        df=baseline,
        quality_report=quality_report(baseline, as_of=date(2026, 8, 21)),
        approved=True,
    )
    candidate = baseline.copy()
    candidate.at[1, "status"] = "inactive"

    second = store.publish(
        dataset_name="doctors",
        df=candidate,
        quality_report=quality_report(candidate, as_of=date(2026, 8, 21)),
        approved=True,
        parent_version_id=first.version_id,
    )

    assert store.active_version("doctors").version_id == second.version_id


def test_publish_is_idempotent_for_repeated_request_key(tmp_path):
    store = VersionStore(tmp_path / "dataops.sqlite3", tmp_path / "versions")
    df = _clean_df()
    report = quality_report(df, as_of=date(2026, 8, 21))

    first = store.publish(
        dataset_name="doctors",
        df=df,
        quality_report=report,
        approved=True,
        idempotency_key="same-approval-request",
    )
    repeated = store.publish(
        dataset_name="doctors",
        df=df,
        quality_report=report,
        approved=True,
        idempotency_key="same-approval-request",
    )

    assert repeated.version_id == first.version_id
    assert len(store.list_versions("doctors")) == 1
    assert store.active_version("doctors").version_id == first.version_id


def test_publish_rejects_idempotency_key_reused_for_different_candidate(tmp_path):
    store = VersionStore(tmp_path / "dataops.sqlite3", tmp_path / "versions")
    df = _clean_df()
    report = quality_report(df, as_of=date(2026, 8, 21))
    store.publish(
        dataset_name="doctors",
        df=df,
        quality_report=report,
        approved=True,
        idempotency_key="approval-request",
    )
    changed = df.copy()
    changed.at[0, "title"] = "Unknown"

    with pytest.raises(IdempotencyConflictError):
        store.publish(
            dataset_name="doctors",
            df=changed,
            quality_report=quality_report(changed, as_of=date(2026, 8, 21)),
            approved=True,
            idempotency_key="approval-request",
        )

    assert len(store.list_versions("doctors")) == 1


def test_concurrent_publish_with_same_expected_active_keeps_single_winner(tmp_path):
    store = VersionStore(tmp_path / "dataops.sqlite3", tmp_path / "versions")
    baseline = _clean_df()
    active = store.publish(
        dataset_name="doctors",
        df=baseline,
        quality_report=quality_report(baseline, as_of=date(2026, 8, 21)),
        approved=True,
    )

    def publish_candidate(index: int) -> tuple[str, str]:
        candidate = baseline.copy()
        candidate.at[0, "title"] = f"Concurrent Title {index}"
        try:
            record = store.publish(
                dataset_name="doctors",
                df=candidate,
                quality_report=quality_report(candidate, as_of=date(2026, 8, 21)),
                approved=True,
                parent_version_id=active.version_id,
                expected_active_version_id=active.version_id,
                require_expected_active_match=True,
                idempotency_key=f"concurrent-request-{index}",
            )
        except ActiveVersionConflictError:
            return ("conflict", "")
        return ("published", record.version_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result()
            for future in as_completed(
                executor.submit(publish_candidate, index) for index in (1, 2)
            )
        ]

    published = [version_id for status, version_id in results if status == "published"]
    conflicts = [status for status, _ in results if status == "conflict"]
    assert len(published) == 1
    assert len(conflicts) == 1
    assert len(store.list_versions("doctors")) == 2
    assert store.active_version("doctors").version_id == published[0]


def _clean_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "doctor_id": "D001",
                "name": "Alice Chen",
                "department": "Cardiology",
                "title": "Chief Physician",
                "status": "active",
                "updated_at": "2026-08-01",
                "source": "hospital_roster",
            },
            {
                "doctor_id": "D002",
                "name": "Ben Wang",
                "department": "Neurology",
                "title": "Attending Physician",
                "status": "active",
                "updated_at": "2026-07-15",
                "source": "hospital_roster",
            },
            {
                "doctor_id": "D003",
                "name": "Cara Li",
                "department": "Pediatrics",
                "title": "Resident Physician",
                "status": "active",
                "updated_at": "2026-06-20",
                "source": "hospital_roster",
            },
            {
                "doctor_id": "D004",
                "name": "David Zhou",
                "department": "Orthopedics",
                "title": "Associate Chief Physician",
                "status": "active",
                "updated_at": "2026-08-10",
                "source": "hospital_roster",
            },
            {
                "doctor_id": "D005",
                "name": "Eva Sun",
                "department": "Dermatology",
                "title": "Attending Physician",
                "status": "active",
                "updated_at": "2026-05-11",
                "source": "hospital_roster",
            },
            {
                "doctor_id": "D006",
                "name": "Frank Xu",
                "department": "Cardiology",
                "title": "Resident Physician",
                "status": "inactive",
                "updated_at": "2026-03-09",
                "source": "hospital_roster",
            },
        ]
    )
