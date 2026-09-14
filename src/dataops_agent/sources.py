from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, BinaryIO
from urllib import request as urllib_request
from urllib.parse import urlparse

import pandas as pd

from dataops_agent.rules import quality_report

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT_DIR = PROJECT_ROOT / "data" / "source_snapshots"
DEFAULT_SOURCE_MANIFEST = PROJECT_ROOT / "data" / "public_sources.json"
DEFAULT_TIMEOUT_SECONDS = 20
SUPPORTED_CANDIDATE_BOUNDARY = "candidate_only_not_auto_published"
SOURCE_MANIFEST_REQUIRED_FIELDS = {
    "id",
    "dataset_name",
    "schema_version",
    "source_url",
    "snapshot_path",
    "metadata_path",
    "candidate_path",
    "candidate_boundary",
}
CMS_HOSPITAL_COLUMN_MAP = {
    "facility_id": "hospital_id",
    "facility_name": "name",
    "citytown": "city",
    "state": "state",
    "hospital_type": "hospital_type",
    "emergency_services": "emergency_services",
    "hospital_overall_rating": "rating",
    "source_url": "source_url",
    "source_checksum": "source_checksum",
}
NYC_311_COLUMN_MAP = {
    "unique_key": "request_id",
    "created_date": "created_at",
    "agency": "agency",
    "complaint_type": "complaint_type",
    "status": "status",
    "borough": "borough",
    "source_url": "source_url",
    "source_checksum": "source_checksum",
}

UrlOpen = Callable[..., BinaryIO]


@dataclass(frozen=True)
class SourceSnapshot:
    source_url: str
    raw_path: Path
    metadata_path: Path
    checksum: str
    content_type: str
    byte_count: int
    fetched_at: str


def fetch_public_source(
    source_url: str,
    *,
    snapshot_dir: Path | str = DEFAULT_SNAPSHOT_DIR,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    urlopen: UrlOpen = urllib_request.urlopen,
) -> SourceSnapshot:
    parsed = urlparse(source_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http and https public sources are supported.")

    request = urllib_request.Request(
        source_url,
        headers={"User-Agent": "DataOpsAgent/0.1 (+local-source-snapshot)"},
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        body = response.read()
        content_type = response.headers.get("Content-Type", "")

    checksum = hashlib.sha256(body).hexdigest()
    suffix = _suffix_for_source(parsed.path, content_type, body)
    snapshot_root = Path(snapshot_dir)
    snapshot_root.mkdir(parents=True, exist_ok=True)
    raw_path = snapshot_root / f"{checksum[:16]}{suffix}"
    metadata_path = snapshot_root / f"{checksum[:16]}.metadata.json"
    fetched_at = datetime.now(UTC).isoformat()

    raw_path.write_bytes(body)
    snapshot = SourceSnapshot(
        source_url=source_url,
        raw_path=raw_path,
        metadata_path=metadata_path,
        checksum=checksum,
        content_type=content_type,
        byte_count=len(body),
        fetched_at=fetched_at,
    )
    metadata_path.write_text(json.dumps(_snapshot_to_json(snapshot), indent=2), encoding="utf-8")
    return snapshot


def extract_snapshot_to_csv(
    snapshot_path: Path | str,
    output_csv: Path | str,
    *,
    metadata_path: Path | str | None = None,
) -> dict[str, Any]:
    source_path = Path(snapshot_path)
    df = _dataframe_from_snapshot(source_path)
    metadata = _load_metadata(metadata_path or _default_metadata_path(source_path))
    if metadata:
        df["source_url"] = str(metadata.get("source_url", ""))
        df["source_checksum"] = str(metadata.get("checksum", ""))

    destination = Path(output_csv)
    destination.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(destination, index=False)
    return {
        "output_path": str(destination),
        "row_count": int(len(df)),
        "columns": [str(column) for column in df.columns],
    }


def normalize_cms_hospitals(input_csv: Path | str, output_csv: Path | str) -> dict[str, Any]:
    df = pd.read_csv(input_csv, dtype=str, keep_default_na=False)
    missing_columns = [column for column in CMS_HOSPITAL_COLUMN_MAP if column not in df.columns]
    if missing_columns:
        raise ValueError(f"CMS hospital source is missing columns: {', '.join(missing_columns)}")

    normalized = df.rename(columns=CMS_HOSPITAL_COLUMN_MAP)[
        list(CMS_HOSPITAL_COLUMN_MAP.values())
    ].copy()
    for column in normalized.columns:
        normalized[column] = normalized[column].astype(str).str.strip()

    destination = Path(output_csv)
    destination.parent.mkdir(parents=True, exist_ok=True)
    normalized.to_csv(destination, index=False)
    return {
        "output_path": str(destination),
        "row_count": int(len(normalized)),
        "columns": [str(column) for column in normalized.columns],
    }


def normalize_nyc_311_service_requests(
    input_csv: Path | str, output_csv: Path | str
) -> dict[str, Any]:
    df = pd.read_csv(input_csv, dtype=str, keep_default_na=False)
    missing_columns = [column for column in NYC_311_COLUMN_MAP if column not in df.columns]
    if missing_columns:
        raise ValueError(f"NYC 311 source is missing columns: {', '.join(missing_columns)}")

    normalized = df.rename(columns=NYC_311_COLUMN_MAP)[list(NYC_311_COLUMN_MAP.values())].copy()
    for column in normalized.columns:
        normalized[column] = normalized[column].astype(str).str.strip()

    destination = Path(output_csv)
    destination.parent.mkdir(parents=True, exist_ok=True)
    normalized.to_csv(destination, index=False)
    return {
        "output_path": str(destination),
        "row_count": int(len(normalized)),
        "columns": [str(column) for column in normalized.columns],
    }


def load_source_manifest(
    manifest_path: Path | str = DEFAULT_SOURCE_MANIFEST,
) -> list[dict[str, Any]]:
    payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Source manifest must be a list.")
    if not all(isinstance(item, dict) for item in payload):
        raise ValueError("Source manifest entries must be objects.")
    return payload


def validate_source_manifest(
    manifest_path: Path | str = DEFAULT_SOURCE_MANIFEST,
    *,
    project_root: Path | str = PROJECT_ROOT,
) -> dict[str, Any]:
    root = Path(project_root)
    issues: list[str] = []
    try:
        sources = load_source_manifest(manifest_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return _manifest_report([], [f"manifest: {exc}"])

    summaries = []
    for index, item in enumerate(sources):
        source_id = str(item.get("id") or f"source[{index}]")
        missing_fields = sorted(SOURCE_MANIFEST_REQUIRED_FIELDS - set(item))
        issues.extend(f"{source_id}: missing field {field}" for field in missing_fields)
        summaries.append(_validate_manifest_item(item, source_id, root, issues))

    return _manifest_report(summaries, issues)


def html_table_records(html_text: str) -> list[dict[str, str]]:
    parser = _FirstTableParser()
    parser.feed(html_text)
    if not parser.tables:
        raise ValueError("No HTML table found in source snapshot.")
    return _table_to_records(parser.tables[0])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Fetch a public data source and extract a CSV.")
    parser.add_argument("--url", help="Public http(s) URL to snapshot.")
    parser.add_argument("--output", help="Candidate CSV output path.")
    parser.add_argument(
        "--normalize",
        choices=["cms_hospitals", "nyc_311_service_requests"],
        help="Normalize a supported public-source schema after extraction.",
    )
    parser.add_argument(
        "--validate-manifest",
        action="store_true",
        help="Validate the tracked public source manifest and local candidate assets.",
    )
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_SOURCE_MANIFEST),
        help="Public source manifest path for --validate-manifest.",
    )
    parser.add_argument(
        "--snapshot-dir",
        default=str(DEFAULT_SNAPSHOT_DIR),
        help="Directory for raw snapshots and metadata.",
    )
    args = parser.parse_args(argv)

    if args.validate_manifest:
        report = validate_source_manifest(args.manifest)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if report["status"] != "ok":
            raise SystemExit(1)
        return
    if not args.url or not args.output:
        parser.error("--url and --output are required unless --validate-manifest is used.")

    try:
        snapshot = fetch_public_source(args.url, snapshot_dir=args.snapshot_dir)
        output_path = Path(args.output)
        extraction_output = output_path.with_suffix(".raw.csv") if args.normalize else output_path
        extraction = extract_snapshot_to_csv(
            snapshot.raw_path,
            extraction_output,
            metadata_path=snapshot.metadata_path,
        )
        normalization = None
        if args.normalize == "cms_hospitals":
            normalization = normalize_cms_hospitals(extraction_output, output_path)
        elif args.normalize == "nyc_311_service_requests":
            normalization = normalize_nyc_311_service_requests(extraction_output, output_path)
    except ValueError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(2) from None

    result = {"status": "ok", "snapshot": _snapshot_to_json(snapshot), "extraction": extraction}
    if normalization:
        result["normalization"] = normalization
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )


class _FirstTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._in_table = False
        self._table_depth = 0
        self._current_table: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell: list[str] = []
        self._in_row = False
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        _ = attrs
        if tag == "table" and not self.tables:
            if not self._in_table:
                self._in_table = True
                self._table_depth = 1
                self._current_table = []
            else:
                self._table_depth += 1
        elif tag == "tr" and self._in_table and self._table_depth == 1:
            self._in_row = True
            self._current_row = []
        elif tag in {"td", "th"} and self._in_row:
            self._in_cell = True
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._in_cell:
            self._current_row.append(_clean_cell("".join(self._current_cell)))
            self._current_cell = []
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if any(cell for cell in self._current_row):
                self._current_table.append(self._current_row)
            self._current_row = []
            self._in_row = False
        elif tag == "table" and self._in_table:
            self._table_depth -= 1
            if self._table_depth == 0:
                self.tables.append(self._current_table)
                self._current_table = []
                self._in_table = False


def _dataframe_from_snapshot(source_path: Path) -> pd.DataFrame:
    suffix = source_path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(source_path, dtype=str, keep_default_na=False)
    if suffix == ".json":
        records = _records_from_json(json.loads(source_path.read_text(encoding="utf-8")))
        return pd.DataFrame(records)
    if suffix in {".html", ".htm", ".txt"}:
        records = html_table_records(source_path.read_text(encoding="utf-8", errors="replace"))
        return pd.DataFrame(records)
    raise ValueError(f"Unsupported source snapshot type: {suffix}")


def _records_from_json(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        if {"errorCode", "message"}.issubset(payload):
            raise ValueError(f"JSON source returned an error payload: {payload['message']}")
        records = next((value for value in payload.values() if isinstance(value, list)), [payload])
    else:
        raise ValueError("JSON source must be an object or list.")
    if all(isinstance(item, dict) for item in records):
        return records
    return [{"value": item} for item in records]


def _table_to_records(rows: list[list[str]]) -> list[dict[str, str]]:
    if len(rows) < 2:
        raise ValueError("HTML table must include a header row and at least one data row.")
    headers = _dedupe_headers([_safe_header(value, index) for index, value in enumerate(rows[0])])
    records: list[dict[str, str]] = []
    for row in rows[1:]:
        padded = (row + [""] * len(headers))[: len(headers)]
        if any(cell for cell in padded):
            records.append(dict(zip(headers, padded, strict=True)))
    if not records:
        raise ValueError("HTML table has no data rows.")
    return records


def _dedupe_headers(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result: list[str] = []
    for header in headers:
        count = seen.get(header, 0)
        seen[header] = count + 1
        result.append(header if count == 0 else f"{header}_{count + 1}")
    return result


def _safe_header(value: str, index: int) -> str:
    normalized = re.sub(r"\s+", "_", value.strip()).strip("_")
    return normalized or f"column_{index + 1}"


def _clean_cell(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _suffix_for_source(url_path: str, content_type: str, body: bytes = b"") -> str:
    suffix = Path(url_path).suffix.lower()
    if suffix in {".csv", ".json", ".html", ".htm", ".txt"}:
        return suffix
    stripped_body = body.lstrip()
    if stripped_body.startswith((b"{", b"[")):
        return ".json"
    normalized = content_type.lower().split(";", 1)[0].strip()
    if normalized in {"text/csv", "application/csv"} or "csv" in normalized:
        return ".csv"
    if normalized == "application/json" or normalized.endswith("+json"):
        return ".json"
    if normalized in {"text/html", "application/xhtml+xml"}:
        return ".html"
    return ".txt"


def _default_metadata_path(source_path: Path) -> Path:
    return source_path.with_name(f"{source_path.stem}.metadata.json")


def _load_metadata(metadata_path: Path | str) -> dict[str, Any]:
    path = Path(metadata_path)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_manifest_item(
    item: dict[str, Any],
    source_id: str,
    root: Path,
    issues: list[str],
) -> dict[str, Any]:
    source_url = str(item.get("source_url", ""))
    if urlparse(source_url).scheme not in {"http", "https"}:
        issues.append(f"{source_id}: source_url must be http(s)")
    schema_version = str(item.get("schema_version", "")).strip()
    if "schema_version" in item and not schema_version:
        issues.append(f"{source_id}: schema_version must be non-empty")
    candidate_boundary = str(item.get("candidate_boundary", "")).strip()
    if "candidate_boundary" in item and candidate_boundary != SUPPORTED_CANDIDATE_BOUNDARY:
        issues.append(
            f"{source_id}: candidate_boundary must be {SUPPORTED_CANDIDATE_BOUNDARY}"
        )

    snapshot_path = _resolve_project_path(root, str(item.get("snapshot_path", "")))
    metadata_path = _resolve_project_path(root, str(item.get("metadata_path", "")))
    candidate_path = _resolve_project_path(root, str(item.get("candidate_path", "")))
    for label, path in {
        "snapshot_path": snapshot_path,
        "metadata_path": metadata_path,
        "candidate_path": candidate_path,
    }.items():
        if not path.is_file():
            issues.append(f"{source_id}: {label} does not exist: {path}")

    metadata = _load_metadata(metadata_path)
    checksum = str(metadata.get("checksum", ""))
    source_checksum_matches_snapshot = False
    if metadata:
        if metadata.get("source_url") != source_url:
            issues.append(f"{source_id}: metadata source_url does not match manifest")
        if snapshot_path.is_file():
            source_checksum_matches_snapshot = _sha256_file(snapshot_path) == checksum
            if not source_checksum_matches_snapshot:
                issues.append(f"{source_id}: metadata checksum does not match snapshot")

    candidate_summary = _validate_candidate_csv(item, source_id, candidate_path, checksum, issues)
    baseline_summary = _baseline_summary(item, root, source_id, issues)
    return {
        "id": source_id,
        "dataset_name": item.get("dataset_name"),
        "schema_version": schema_version,
        "source_owner": item.get("source_owner", ""),
        "source_type": item.get("source_type", ""),
        "candidate_boundary": candidate_boundary,
        "candidate_path": _project_relative(root, candidate_path),
        "snapshot_path": _project_relative(root, snapshot_path),
        "metadata_path": _project_relative(root, metadata_path),
        "source_checksum": checksum,
        "source_checksum_matches_snapshot": source_checksum_matches_snapshot,
        **candidate_summary,
        **baseline_summary,
    }


def _validate_candidate_csv(
    item: dict[str, Any],
    source_id: str,
    candidate_path: Path,
    checksum: str,
    issues: list[str],
) -> dict[str, Any]:
    if not candidate_path.is_file():
        return {
            "row_count": 0,
            "columns": [],
            "candidate_quality_gate_passed": False,
        }
    df = pd.read_csv(candidate_path, dtype=str, keep_default_na=False)
    dataset_name = str(item.get("dataset_name") or "")
    report = quality_report(df, dataset_name=dataset_name)
    if not report["passes_quality_gate"]:
        issues.append(f"{source_id}: candidate CSV does not pass quality gate")

    if "source_url" not in df.columns or "source_checksum" not in df.columns:
        issues.append(f"{source_id}: candidate CSV must include source_url/source_checksum")
    elif checksum:
        checksums = set(df["source_checksum"].astype(str))
        if checksums != {checksum}:
            issues.append(f"{source_id}: candidate source_checksum does not match metadata")
    return {
        "row_count": int(len(df)),
        "columns": [str(column) for column in df.columns],
        "issue_count": int(report["issue_count"]),
        "candidate_quality_gate_passed": bool(report["passes_quality_gate"]),
    }


def _baseline_summary(
    item: dict[str, Any],
    root: Path,
    source_id: str,
    issues: list[str],
) -> dict[str, Any]:
    baseline_path = item.get("baseline_report_path")
    if not baseline_path:
        return {}
    path = _resolve_project_path(root, str(baseline_path))
    if not path.is_file():
        issues.append(f"{source_id}: baseline_report_path does not exist: {path}")
        return {"baseline_report_path": _project_relative(root, path)}
    report = json.loads(path.read_text(encoding="utf-8"))
    missing_metrics = {"precision", "recall", "f1"} - set(report)
    issues.extend(
        f"{source_id}: baseline report missing metric {metric}" for metric in missing_metrics
    )
    return {
        "baseline_report_path": _project_relative(root, path),
        "baseline_metrics": {
            key: report.get(key)
            for key in ("precision", "recall", "f1")
            if key in report
        },
    }


def _manifest_report(sources: list[dict[str, Any]], issues: list[str]) -> dict[str, Any]:
    return {
        "status": "ok" if not issues else "failed",
        "source_count": len(sources),
        "datasets": sorted({str(item.get("dataset_name")) for item in sources}),
        "issue_count": len(issues),
        "issues": issues,
        "sources": sources,
    }


def _resolve_project_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _project_relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_to_json(snapshot: SourceSnapshot) -> dict[str, Any]:
    payload = asdict(snapshot)
    payload["raw_path"] = str(snapshot.raw_path)
    payload["metadata_path"] = str(snapshot.metadata_path)
    return payload


if __name__ == "__main__":
    main()
