from __future__ import annotations

import hashlib
import json

from dataops_agent.freshness import run_freshness_report


def test_freshness_report_builds_dry_run_schedule_from_manifest(tmp_path):
    report = run_freshness_report(
        report_json=tmp_path / "freshness.json",
        report_md=tmp_path / "freshness.md",
        now="2026-08-23T12:00:00+00:00",
        run_id="test-freshness",
    )

    assert report["status"] == "ok"
    assert report["scope"]["network_called"] is False
    assert report["scope"]["dry_run_only"] is True
    assert report["metrics"]["source_count"] == 2
    assert report["metrics"]["fresh_source_count"] == 2
    assert report["metrics"]["stale_source_count"] == 0
    assert len(report["dry_run_schedule"]) == 2
    assert report["metrics"]["failure_simulation_pass_count"] == 3
    assert "新鲜度" in (tmp_path / "freshness.md").read_text(encoding="utf-8")


def test_freshness_report_marks_stale_source_without_auto_publish(tmp_path):
    manifest_path = _write_source_fixture(tmp_path, fetched_at="2026-08-01T00:00:00+00:00")

    report = run_freshness_report(
        manifest_path=manifest_path,
        report_json=tmp_path / "freshness.json",
        report_md=tmp_path / "freshness.md",
        project_root=tmp_path,
        now="2026-08-02T00:00:00+00:00",
        policy_by_dataset={
            "service_requests": {"max_age_hours": 1, "schedule_interval_hours": 1}
        },
    )

    source = report["sources"][0]
    assert report["status"] == "ok"
    assert source["freshness_status"] == "stale"
    assert source["release_gate_passed"] is False
    assert source["recommended_action"] == "refresh_candidate_before_publish"
    assert "保留上一版" in source["compensation"]


def test_freshness_report_blocks_checksum_mismatch(tmp_path):
    manifest_path = _write_source_fixture(
        tmp_path,
        fetched_at="2026-08-01T00:00:00+00:00",
        metadata_checksum="bad-checksum",
    )

    report = run_freshness_report(
        manifest_path=manifest_path,
        report_json=tmp_path / "freshness.json",
        report_md=tmp_path / "freshness.md",
        project_root=tmp_path,
        now="2026-08-01T01:00:00+00:00",
    )

    assert report["status"] == "failed"
    assert report["metrics"]["blocked_source_count"] == 1
    assert "metadata checksum does not match snapshot" in report["issues"][0]


def _write_source_fixture(
    tmp_path,
    *,
    fetched_at: str,
    metadata_checksum: str | None = None,
):
    snapshot_path = tmp_path / "data" / "source_snapshots" / "snapshot.json"
    metadata_path = tmp_path / "data" / "source_snapshots" / "snapshot.metadata.json"
    candidate_path = tmp_path / "data" / "imported" / "candidate.csv"
    snapshot_path.parent.mkdir(parents=True)
    candidate_path.parent.mkdir(parents=True)
    body = b'{"records": [{"id": "1"}]}'
    snapshot_path.write_bytes(body)
    checksum = hashlib.sha256(body).hexdigest()
    metadata_path.write_text(
        json.dumps(
            {
                "source_url": "https://example.com/data.json",
                "checksum": metadata_checksum or checksum,
                "fetched_at": fetched_at,
            }
        ),
        encoding="utf-8",
    )
    candidate_path.write_text("id,source_checksum\n1," + checksum + "\n", encoding="utf-8")
    manifest_path = tmp_path / "public_sources.json"
    manifest_path.write_text(
        json.dumps(
            [
                {
                    "id": "fixture_source",
                    "dataset_name": "service_requests",
                    "source_owner": "fixture",
                    "source_url": "https://example.com/data.json",
                    "snapshot_path": snapshot_path.relative_to(tmp_path).as_posix(),
                    "metadata_path": metadata_path.relative_to(tmp_path).as_posix(),
                    "candidate_path": candidate_path.relative_to(tmp_path).as_posix(),
                }
            ]
        ),
        encoding="utf-8",
    )
    return manifest_path
