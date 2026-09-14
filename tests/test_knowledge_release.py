from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pandas as pd
import pytest

from dataops_agent.knowledge_release import write_knowledge_release


def test_knowledge_release_exports_rag_ready_jsonl_for_public_service_requests(tmp_path):
    output_jsonl = tmp_path / "service_requests.jsonl"
    manifest_json = tmp_path / "manifest.json"

    manifest = write_knowledge_release(
        dataset_path=Path("data/imported/nyc_311_service_requests_sample.csv"),
        dataset_name="service_requests",
        version_id="test-service-requests-v1",
        output_jsonl=output_jsonl,
        manifest_json=manifest_json,
    )

    docs = [json.loads(line) for line in output_jsonl.read_text(encoding="utf-8").splitlines()]
    assert manifest["status"] == "ok"
    assert manifest["doc_count"] == 500
    assert len(docs) == 500
    assert docs[0]["dataset_name"] == "service_requests"
    assert docs[0]["schema_version"] == "service_requests_v1"
    assert docs[0]["version_id"] == "test-service-requests-v1"
    assert "complaint_type:" in docs[0]["content"]
    assert manifest["rag_boundary"]["dataops_builds_embedding"] is False
    assert manifest["rag_boundary"]["dataops_writes_vector_db"] is False


def test_knowledge_release_requires_quality_gate(tmp_path):
    dirty = tmp_path / "dirty.csv"
    pd.DataFrame(
        [
            {
                "request_id": "REQ-1",
                "created_at": "2026-08-22",
                "agency": "DOT",
                "complaint_type": "Noise",
                "status": "Not Real",
                "borough": "QUEENS",
                "source_url": "https://example.test",
                "source_checksum": "abc",
            }
        ]
    ).to_csv(dirty, index=False)

    with pytest.raises(ValueError, match="quality gate"):
        write_knowledge_release(
            dataset_path=dirty,
            dataset_name="service_requests",
            version_id="dirty",
            output_jsonl=tmp_path / "dirty.jsonl",
            manifest_json=tmp_path / "dirty_manifest.json",
        )


def test_knowledge_release_has_packaged_entrypoint():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert config["project"]["scripts"]["dataops-agent-knowledge-release"] == (
        "dataops_agent.knowledge_release:main"
    )
