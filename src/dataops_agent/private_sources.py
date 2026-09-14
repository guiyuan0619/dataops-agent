from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from dataops_agent.rules import quality_report

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_CSV = (
    PROJECT_ROOT / "data" / "private_exports" / "enterprise_service_tickets_authorized_sample.csv"
)
DEFAULT_CANDIDATE_CSV = (
    PROJECT_ROOT / "data" / "imported" / "authorized_service_requests_sample.csv"
)
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "dataops_private_source_report.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "dataops_private_source_report.md"

REQUIRED_EXPORT_COLUMNS = {
    "ticket_id",
    "opened_at",
    "business_unit",
    "issue_type",
    "lifecycle_status",
    "region",
    "visibility",
    "redaction_status",
    "authorization_note",
}
STATUS_MAP = {
    "open": "Open",
    "in_progress": "In Progress",
    "pending": "Pending",
    "started": "Started",
    "assigned": "Assigned",
    "closed": "Closed",
}
BOROUGH_MAP = {
    "bronx": "BRONX",
    "brooklyn": "BROOKLYN",
    "manhattan": "MANHATTAN",
    "queens": "QUEENS",
    "staten island": "STATEN ISLAND",
    "unspecified": "Unspecified",
}
SECRET_COLUMN_NAMES = {"api_key", "access_token", "secret", "password", "private_key", "token"}
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_.-]{16,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9_.-]{12,}", re.IGNORECASE),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"password\s*[:=]\s*\S+", re.IGNORECASE),
]

PRIVATE_SOURCE_METADATA = {
    "source_id": "enterprise_service_tickets_authorized_sample",
    "dataset_name": "service_requests",
    "schema_version": "service_requests_v1",
    "source_type": "authorized_private_export_fixture",
    "source_owner": "Example Enterprise Operations",
    "authorization_note": (
        "脱敏模拟授权导出，用于证明私有数据 adapter、元数据、脱敏和候选边界。"
    ),
    "data_visibility": "de_identified_fixture",
    "redaction_policy": "remove direct personal identifiers before DataOps ingestion",
    "credential_policy": "credentials_not_persisted",
    "candidate_boundary": "candidate_only_not_auto_published",
}


def run_private_source_report(
    *,
    input_csv: Path | str = DEFAULT_INPUT_CSV,
    candidate_csv: Path | str = DEFAULT_CANDIDATE_CSV,
    report_json: Path | str = DEFAULT_REPORT_JSON,
    report_md: Path | str = DEFAULT_REPORT_MD,
) -> dict[str, Any]:
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    input_path = Path(input_csv)
    candidate_path = Path(candidate_csv)
    source_checksum = _sha256_file(input_path)
    raw = pd.read_csv(input_path, dtype=str, keep_default_na=False)
    schema_check = _validate_export_schema(raw)
    credential_scan = _credential_scan(input_path, raw)

    normalization: dict[str, Any] | None = None
    candidate_quality_report: dict[str, Any] | None = None
    if schema_check["passed"] and not credential_scan["credential_leak_detected"]:
        normalized = normalize_authorized_service_tickets(
            raw,
            source_checksum=source_checksum,
        )
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        normalized.to_csv(candidate_path, index=False)
        candidate_quality_report = quality_report(normalized, dataset_name="service_requests")
        normalization = {
            "candidate_path": str(candidate_path),
            "row_count": int(len(normalized)),
            "columns": [str(column) for column in normalized.columns],
        }

    report = {
        "status": _status(schema_check, credential_scan, candidate_quality_report),
        "generated_at": generated_at,
        "source": {
            **PRIVATE_SOURCE_METADATA,
            "input_csv": str(input_path),
            "input_checksum": source_checksum,
            "raw_row_count": int(len(raw)),
        },
        "outputs": {
            "candidate_csv": str(candidate_path),
            "report_json": str(Path(report_json)),
            "report_md": str(Path(report_md)),
        },
        "scope": {
            "local_only": True,
            "ecs_used": False,
            "qwen_used": False,
            "real_private_network_connected": False,
            "production_traffic": False,
            "raw_credentials_recorded": False,
        },
        "schema_check": schema_check,
        "credential_scan": credential_scan,
        "normalization": normalization,
        "quality_report": candidate_quality_report,
        "boundary": {
            "can_say": [
                "实现了授权私有导出到通用 service_requests 合同的本地 adapter。",
                "报告记录 source_owner、授权说明、数据可见性、脱敏策略、凭证不落盘策略和候选边界。",
                "候选数据通过现有 DataOps 质量门禁，但不会自动发布。",
            ],
            "cannot_say": [
                "不能说接入了真实企业内网、真实客户库、真实医院库或真实生产数据库。",
                "不能说这份脱敏模拟数据代表真实企业数据规模或业务 ROI。",
                "不能说私有数据可以绕过人工审批直接发布。",
            ],
        },
    }
    _write_json(report_json, report)
    _write_markdown(report_md, report)
    return report


def normalize_authorized_service_tickets(
    raw: pd.DataFrame,
    *,
    source_checksum: str,
) -> pd.DataFrame:
    missing = sorted(REQUIRED_EXPORT_COLUMNS - set(raw.columns))
    if missing:
        raise ValueError(f"Private export is missing columns: {', '.join(missing)}")

    normalized = pd.DataFrame(
        {
            "request_id": raw["ticket_id"].astype(str).str.strip(),
            "created_at": raw["opened_at"].astype(str).str.strip(),
            "agency": raw["business_unit"].astype(str).str.strip(),
            "complaint_type": raw["issue_type"].astype(str).str.strip(),
            "status": raw["lifecycle_status"].map(_normalize_status),
            "borough": raw["region"].map(_normalize_region),
            "source_url": "private://authorized/enterprise_service_tickets",
            "source_checksum": source_checksum,
        }
    )
    return normalized


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run DataOps private source adapter report.")
    parser.add_argument("--input-csv", default=str(DEFAULT_INPUT_CSV))
    parser.add_argument("--candidate-csv", default=str(DEFAULT_CANDIDATE_CSV))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    args = parser.parse_args(argv)
    report = run_private_source_report(
        input_csv=args.input_csv,
        candidate_csv=args.candidate_csv,
        report_json=args.report_json,
        report_md=args.report_md,
    )
    print(json.dumps(_stdout_summary(report), ensure_ascii=False, indent=2))
    if report["status"] != "ok":
        raise SystemExit(1)


def _validate_export_schema(raw: pd.DataFrame) -> dict[str, Any]:
    missing = sorted(REQUIRED_EXPORT_COLUMNS - set(raw.columns))
    unexpected_secret_columns = sorted(
        column
        for column in raw.columns
        if column.strip().lower().replace("-", "_") in SECRET_COLUMN_NAMES
    )
    return {
        "passed": not missing and not unexpected_secret_columns,
        "missing_columns": missing,
        "unexpected_secret_columns": unexpected_secret_columns,
        "required_columns": sorted(REQUIRED_EXPORT_COLUMNS),
    }


def _credential_scan(input_path: Path, raw: pd.DataFrame) -> dict[str, Any]:
    text = input_path.read_text(encoding="utf-8", errors="replace")
    pattern_hits = [pattern.pattern for pattern in SECRET_PATTERNS if pattern.search(text)]
    secret_columns = sorted(
        column
        for column in raw.columns
        if column.strip().lower().replace("-", "_") in SECRET_COLUMN_NAMES
    )
    return {
        "credential_leak_detected": bool(pattern_hits or secret_columns),
        "secret_pattern_hits": pattern_hits,
        "secret_columns": secret_columns,
        "scanned_file": str(input_path),
    }


def _status(
    schema_check: dict[str, Any],
    credential_scan: dict[str, Any],
    candidate_quality_report: dict[str, Any] | None,
) -> str:
    if not schema_check["passed"]:
        return "failed_schema"
    if credential_scan["credential_leak_detected"]:
        return "failed_credential_scan"
    if not candidate_quality_report or not candidate_quality_report["passes_quality_gate"]:
        return "failed_quality_gate"
    return "ok"


def _normalize_status(value: str) -> str:
    return STATUS_MAP.get(str(value).strip().lower(), "Pending")


def _normalize_region(value: str) -> str:
    return BOROUGH_MAP.get(str(value).strip().lower(), "Unspecified")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_markdown(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = report["source"]
    quality = report["quality_report"] or {}
    lines = [
        "# DataOps Agent 私有/授权数据源适配报告",
        "",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 结果：`{report['status']}`",
        "- 范围：本地脱敏模拟授权导出，未启用 ECS，未调用 Qwen，未连接真实企业内网。",
        "",
        "## 来源治理",
        "",
        "| 字段 | 值 |",
        "|---|---|",
        f"| source_owner | {source['source_owner']} |",
        f"| source_type | {source['source_type']} |",
        f"| data_visibility | {source['data_visibility']} |",
        f"| redaction_policy | {source['redaction_policy']} |",
        f"| credential_policy | {source['credential_policy']} |",
        f"| candidate_boundary | {source['candidate_boundary']} |",
        f"| raw_row_count | {source['raw_row_count']} |",
        "",
        "## 检查结果",
        "",
        "| 检查 | 结果 |",
        "|---|---|",
        f"| schema_check | {report['schema_check']['passed']} |",
        f"| credential_leak_detected | {report['credential_scan']['credential_leak_detected']} |",
        f"| quality_gate | {quality.get('passes_quality_gate')} |",
        f"| quality_issue_count | {quality.get('issue_count')} |",
        "",
        "## 面试口径",
        "",
        (
            "- 可以说：实现了授权私有导出到通用数据合同的本地 adapter，"
            "记录授权、脱敏、凭证不落盘和候选边界。"
        ),
        "- 不能说：接入真实企业内网、真实客户库、真实医院库、真实生产数据库或真实业务 ROI。",
    ]
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _stdout_summary(report: dict[str, Any]) -> dict[str, Any]:
    quality = report["quality_report"] or {}
    return {
        "status": report["status"],
        "raw_row_count": report["source"]["raw_row_count"],
        "candidate_row_count": (report["normalization"] or {}).get("row_count"),
        "quality_gate": quality.get("passes_quality_gate"),
        "report_json": report["outputs"]["report_json"],
        "report_md": report["outputs"]["report_md"],
    }


if __name__ == "__main__":
    main()
