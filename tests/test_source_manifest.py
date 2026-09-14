from __future__ import annotations

import hashlib
import json

from dataops_agent.sources import (
    DEFAULT_SOURCE_MANIFEST,
    load_source_manifest,
    validate_source_manifest,
)


def test_public_source_manifest_is_traceable_and_quality_gated():
    manifest = load_source_manifest(DEFAULT_SOURCE_MANIFEST)
    report = validate_source_manifest(DEFAULT_SOURCE_MANIFEST)

    assert {item["dataset_name"] for item in manifest} == {"hospitals", "service_requests"}
    assert report["status"] == "ok"
    assert report["source_count"] == 2
    assert set(report["datasets"]) == {"hospitals", "service_requests"}
    assert {source["schema_version"] for source in report["sources"]} == {
        "hospitals_v1",
        "service_requests_v1",
    }
    assert {
        source["candidate_boundary"] for source in report["sources"]
    } == {"candidate_only_not_auto_published"}
    assert all(source["source_checksum_matches_snapshot"] for source in report["sources"])
    assert all(source["candidate_quality_gate_passed"] for source in report["sources"])
    row_counts = {source["dataset_name"]: source["row_count"] for source in report["sources"]}
    assert row_counts == {"hospitals": 50, "service_requests": 500}


def test_source_manifest_detects_bad_checksum(tmp_path):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text('[{"id":"1","value":"ok"}]', encoding="utf-8")
    checksum = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    metadata = tmp_path / "snapshot.metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "source_url": "https://example.gov/data.json",
                "checksum": f"bad-{checksum}",
            }
        ),
        encoding="utf-8",
    )
    candidate = tmp_path / "candidate.csv"
    candidate.write_text(
        "request_id,created_at,agency,complaint_type,status,borough,source_url,source_checksum\n"
        "REQ-1,2026-08-21,NYPD,Noise,Open,QUEENS,https://example.gov/data.json,bad\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "id": "bad_source",
                    "dataset_name": "service_requests",
                    "schema_version": "service_requests_v1",
                    "source_url": "https://example.gov/data.json",
                    "snapshot_path": str(snapshot),
                    "metadata_path": str(metadata),
                    "candidate_path": str(candidate),
                    "candidate_boundary": "candidate_only_not_auto_published",
                }
            ]
        ),
        encoding="utf-8",
    )

    report = validate_source_manifest(manifest, project_root=tmp_path)

    assert report["status"] == "failed"
    assert "bad_source: metadata checksum does not match snapshot" in report["issues"]
    assert "bad_source: candidate source_checksum does not match metadata" in report["issues"]


def test_source_manifest_rejects_published_candidate_boundary(tmp_path):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text('[{"id":"1","value":"ok"}]', encoding="utf-8")
    checksum = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    metadata = tmp_path / "snapshot.metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "source_url": "https://example.gov/data.json",
                "checksum": checksum,
            }
        ),
        encoding="utf-8",
    )
    candidate = tmp_path / "candidate.csv"
    candidate.write_text(
        "request_id,created_at,agency,complaint_type,status,borough,source_url,source_checksum\n"
        f"REQ-1,2026-08-21,NYPD,Noise,Open,QUEENS,https://example.gov/data.json,{checksum}\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "id": "published_boundary",
                    "dataset_name": "service_requests",
                    "schema_version": "service_requests_v1",
                    "source_url": "https://example.gov/data.json",
                    "snapshot_path": str(snapshot),
                    "metadata_path": str(metadata),
                    "candidate_path": str(candidate),
                    "candidate_boundary": "already_published",
                }
            ]
        ),
        encoding="utf-8",
    )

    report = validate_source_manifest(manifest, project_root=tmp_path)

    assert report["status"] == "failed"
    assert (
        "published_boundary: candidate_boundary must be candidate_only_not_auto_published"
        in report["issues"]
    )
