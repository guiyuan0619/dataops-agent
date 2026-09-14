from __future__ import annotations

import hashlib
import json

import pandas as pd
import pytest

from dataops_agent.sources import (
    extract_snapshot_to_csv,
    fetch_public_source,
    normalize_cms_hospitals,
)


class _FakeResponse:
    def __init__(self, body: bytes, content_type: str) -> None:
        self._body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_fetch_and_extract_html_table_preserves_provenance(tmp_path):
    body = b"""
    <html><body>
      <table>
        <tr><th>doctor_id</th><th>name</th></tr>
        <tr><td>D001</td><td>Alice Chen</td></tr>
      </table>
    </body></html>
    """

    def fake_urlopen(request, timeout):
        assert request.full_url == "https://example.gov/public/doctors"
        assert timeout == 20
        return _FakeResponse(body, "text/html; charset=utf-8")

    snapshot = fetch_public_source(
        "https://example.gov/public/doctors",
        snapshot_dir=tmp_path / "snapshots",
        urlopen=fake_urlopen,
    )
    output = tmp_path / "imported" / "doctors.csv"
    extraction = extract_snapshot_to_csv(
        snapshot.raw_path,
        output,
        metadata_path=snapshot.metadata_path,
    )

    checksum = hashlib.sha256(body).hexdigest()
    metadata = json.loads(snapshot.metadata_path.read_text(encoding="utf-8"))
    df = pd.read_csv(output, dtype=str, keep_default_na=False)

    assert snapshot.checksum == checksum
    assert metadata["source_url"] == "https://example.gov/public/doctors"
    assert extraction["row_count"] == 1
    assert df.to_dict(orient="records") == [
        {
            "doctor_id": "D001",
            "name": "Alice Chen",
            "source_url": "https://example.gov/public/doctors",
            "source_checksum": checksum,
        }
    ]


def test_extract_json_records_to_csv(tmp_path):
    snapshot = tmp_path / "records.json"
    snapshot.write_text(
        json.dumps({"records": [{"name": "Alice Chen", "department": "Cardiology"}]}),
        encoding="utf-8",
    )
    metadata = tmp_path / "records.metadata.json"
    metadata.write_text(
        json.dumps({"source_url": "https://example.gov/data.json", "checksum": "abc123"}),
        encoding="utf-8",
    )

    output = tmp_path / "records.csv"
    extraction = extract_snapshot_to_csv(snapshot, output, metadata_path=metadata)
    df = pd.read_csv(output, dtype=str, keep_default_na=False)

    assert extraction["columns"] == ["name", "department", "source_url", "source_checksum"]
    assert df.iloc[0].to_dict() == {
        "name": "Alice Chen",
        "department": "Cardiology",
        "source_url": "https://example.gov/data.json",
        "source_checksum": "abc123",
    }


def test_fetch_sniffs_json_when_content_type_is_wrong(tmp_path):
    body = b'{"records":[{"name":"Alice Chen"}]}'

    def fake_urlopen(request, timeout):
        return _FakeResponse(body, "application/xhtml+xml;charset=UTF-8")

    snapshot = fetch_public_source(
        "https://example.gov/api/service.xhtml",
        snapshot_dir=tmp_path / "snapshots",
        urlopen=fake_urlopen,
    )

    assert snapshot.raw_path.suffix == ".json"


def test_json_error_payload_is_not_treated_as_data(tmp_path):
    snapshot = tmp_path / "error.json"
    snapshot.write_text(
        json.dumps({"errorCode": "10009", "message": "appKey is required"}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="error payload"):
        extract_snapshot_to_csv(snapshot, tmp_path / "out.csv")


def test_normalize_cms_hospitals_rejects_missing_source_columns(tmp_path):
    source = tmp_path / "bad_cms.csv"
    source.write_text("facility_id,facility_name\n010001,Hospital\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing columns"):
        normalize_cms_hospitals(source, tmp_path / "out.csv")


def test_fetch_rejects_non_http_sources(tmp_path):
    with pytest.raises(ValueError, match="Only http and https"):
        fetch_public_source("file:///etc/passwd", snapshot_dir=tmp_path)
