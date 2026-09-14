from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dataops_agent.consumer_gate import (
    CONSUMER_KEY_FIELDS,
    CONSUMER_SIGNATURE_FIELDS,
)
from dataops_agent.freshness import DEFAULT_FRESHNESS_POLICY, DEFAULT_POLICY
from dataops_agent.integration import GENERIC_CONTRACT_NAME
from dataops_agent.rules import (
    DATASET_ALIASES,
    DATASET_SCHEMA_VERSIONS,
    ENUM_RULES_BY_DATASET,
    KEY_COLUMN_BY_DATASET,
    REQUIRED_COLUMNS_BY_DATASET,
    STALE_AFTER_DAYS,
)
from dataops_agent.sources import (
    DEFAULT_SOURCE_MANIFEST,
    PROJECT_ROOT,
    SOURCE_MANIFEST_REQUIRED_FIELDS,
    SUPPORTED_CANDIDATE_BOUNDARY,
    load_source_manifest,
    validate_source_manifest,
)

DEFAULT_CONTRACT_JSON = PROJECT_ROOT / "reports" / "dataops_dataset_contracts.json"
DEFAULT_CONTRACT_MARKDOWN = PROJECT_ROOT / "reports" / "dataops_dataset_contracts.md"
QUALITY_DIMENSIONS = (
    "completeness",
    "uniqueness",
    "validity",
    "consistency",
    "timeliness",
    "accuracy",
)


def dataset_contract(dataset_name: str) -> dict[str, Any]:
    dataset = dataset_key(dataset_name)
    return {
        "dataset_name": dataset,
        "schema_version": DATASET_SCHEMA_VERSIONS[dataset],
        "aliases": sorted(alias for alias, target in DATASET_ALIASES.items() if target == dataset),
        "required_columns": REQUIRED_COLUMNS_BY_DATASET[dataset],
        "key_column": KEY_COLUMN_BY_DATASET[dataset],
        "enum_rules": _enum_rules(dataset),
        "quality_rules": _quality_rules(dataset),
        "quality_dimension_coverage": _quality_dimension_coverage(dataset),
        "metadata_contract": _metadata_contract(dataset),
        "lineage_boundary": _lineage_boundary(dataset),
        "consumer_contract": {
            "name": GENERIC_CONTRACT_NAME,
            "key_field": CONSUMER_KEY_FIELDS.get(dataset),
            "signature_fields": list(CONSUMER_SIGNATURE_FIELDS.get(dataset, ())),
        },
        "source_assets": _source_assets(dataset),
    }


def dataset_contracts() -> list[dict[str, Any]]:
    return [dataset_contract(dataset) for dataset in sorted(REQUIRED_COLUMNS_BY_DATASET)]


def build_contract_report(
    *,
    manifest_path: Path | str = DEFAULT_SOURCE_MANIFEST,
    validate_manifest: bool = True,
) -> dict[str, Any]:
    manifest_report = (
        validate_source_manifest(manifest_path)
        if validate_manifest
        else {"status": "skipped", "issue_count": None, "issues": []}
    )
    return {
        "status": "ok" if manifest_report["status"] in {"ok", "skipped"} else "failed",
        "generated_at": datetime.now(UTC).isoformat(),
        "contract_name": GENERIC_CONTRACT_NAME,
        "dataset_count": len(REQUIRED_COLUMNS_BY_DATASET),
        "quality_dimensions": list(QUALITY_DIMENSIONS),
        "candidate_boundary": SUPPORTED_CANDIDATE_BOUNDARY,
        "manifest_validation": manifest_report,
        "contracts": dataset_contracts(),
        "boundaries": [
            "Data contracts are checked before approval and publish.",
            "Source snapshots, metadata, and checksums prove candidate origin.",
            "Consumer regression checks key coverage and consumer-visible signatures.",
            (
                "Full column-level lineage, scheduler lineage, and production "
                "data catalog are not claimed."
            ),
        ],
    }


def write_contract_report(
    json_path: Path | str = DEFAULT_CONTRACT_JSON,
    markdown_path: Path | str = DEFAULT_CONTRACT_MARKDOWN,
) -> dict[str, str]:
    report = build_contract_report()
    json_destination = Path(json_path)
    markdown_destination = Path(markdown_path)
    json_destination.parent.mkdir(parents=True, exist_ok=True)
    markdown_destination.parent.mkdir(parents=True, exist_ok=True)
    json_destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_destination.write_text(_to_markdown(report), encoding="utf-8")
    return {
        "json_path": str(json_destination),
        "markdown_path": str(markdown_destination),
        "status": str(report["status"]),
    }


def dataset_key(dataset_name: str) -> str:
    dataset = DATASET_ALIASES.get(dataset_name, dataset_name)
    if dataset not in REQUIRED_COLUMNS_BY_DATASET:
        raise ValueError(f"Unsupported dataset_name: {dataset_name}")
    return dataset


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate DataOps dataset contract reports.")
    parser.add_argument("--json", default=str(DEFAULT_CONTRACT_JSON), help="JSON report path.")
    parser.add_argument(
        "--markdown",
        default=str(DEFAULT_CONTRACT_MARKDOWN),
        help="Markdown report path.",
    )
    args = parser.parse_args(argv)
    result = write_contract_report(args.json, args.markdown)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "ok":
        raise SystemExit(1)


def _enum_rules(dataset: str) -> dict[str, list[str]]:
    return {field: sorted(values) for field, values in ENUM_RULES_BY_DATASET[dataset].items()}


def _quality_rules(dataset: str) -> list[str]:
    rules = [
        "required columns must exist",
        "required fields must be non-empty",
        "dataset key must be unique",
        "enum fields must use allowed values",
        "source_url and source_checksum must stay with public-source candidates",
    ]
    if dataset == "doctors":
        rules.append(f"active doctor records older than {STALE_AFTER_DAYS} days are stale")
    return rules


def _quality_dimension_coverage(dataset: str) -> dict[str, dict[str, str]]:
    coverage = {
        "completeness": {
            "status": "supported",
            "implementation": "required columns and non-empty required fields",
        },
        "uniqueness": {
            "status": "supported",
            "implementation": f"duplicate check on {KEY_COLUMN_BY_DATASET[dataset]}",
        },
        "validity": {
            "status": "supported",
            "implementation": "schema columns and allowed enum values",
        },
        "consistency": {
            "status": "partially_supported",
            "implementation": "candidate checksum, source checksum, and consumer key coverage",
        },
        "timeliness": {
            "status": "partially_supported",
            "implementation": "doctor stale-date rule plus source fetched_at metadata",
        },
        "accuracy": {
            "status": "not_claimed",
            "implementation": "requires authoritative business validation or human review",
        },
    }
    if dataset != "doctors":
        policy = {**DEFAULT_POLICY, **DEFAULT_FRESHNESS_POLICY.get(dataset, {})}
        coverage["timeliness"] = {
            "status": "partially_supported",
            "implementation": (
                "source fetched_at/checksum plus local freshness report; "
                f"check interval {policy['schedule_interval_hours']}h, "
                f"max age {policy['max_age_hours']}h; no production freshness SLA"
            ),
        }
    return coverage


def _metadata_contract(dataset: str) -> dict[str, Any]:
    return {
        "manifest_fields": sorted(SOURCE_MANIFEST_REQUIRED_FIELDS),
        "candidate_required_metadata_columns": ["source_url", "source_checksum"]
        if dataset != "doctors"
        else [],
        "version_metadata": [
            "version_id",
            "dataset_name",
            "path",
            "checksum",
            "created_at",
            "active",
        ],
        "audit_metadata": ["trace_id", "thread_id", "operation", "consumer_agent", "status"],
    }


def _lineage_boundary(dataset: str) -> dict[str, Any]:
    return {
        "supported_lineage": [
            "source_url -> raw snapshot -> metadata checksum -> normalized candidate CSV",
            "candidate CSV -> quality report -> approval checkpoint -> version record",
            "version record -> consumer regression -> active version or rollback target",
        ],
        "dataset_key": KEY_COLUMN_BY_DATASET[dataset],
        "not_claimed": [
            "column-level transformation graph",
            "cross-table warehouse lineage",
            "scheduled production lineage catalog",
        ],
    }


def _source_assets(dataset: str) -> list[dict[str, str]]:
    try:
        manifest = load_source_manifest()
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    assets = []
    for item in manifest:
        if item.get("dataset_name") != dataset:
            continue
        assets.append(
            {
                "id": str(item.get("id", "")),
                "source_type": str(item.get("source_type", "")),
                "source_owner": str(item.get("source_owner", "")),
                "schema_version": str(item.get("schema_version", "")),
                "snapshot_path": str(item.get("snapshot_path", "")),
                "metadata_path": str(item.get("metadata_path", "")),
                "candidate_path": str(item.get("candidate_path", "")),
                "candidate_boundary": str(item.get("candidate_boundary", "")),
            }
        )
    return assets


def _to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# DataOps Dataset Contract Report",
        "",
        f"- Status: `{report['status']}`",
        f"- Contract: `{report['contract_name']}`",
        f"- Dataset count: `{report['dataset_count']}`",
        f"- Candidate boundary: `{report['candidate_boundary']}`",
        f"- Manifest validation: `{report['manifest_validation']['status']}`",
        "",
        "## Dataset Contracts",
        "",
        "| Dataset | Schema | Key | Required columns | Enum fields | Consumer signature |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for contract in report["contracts"]:
        enum_fields = ", ".join(contract["enum_rules"]) or "-"
        signature_fields = ", ".join(contract["consumer_contract"]["signature_fields"]) or "-"
        lines.append(
            "| {dataset} | {schema} | {key} | {required} | {enums} | {signature} |".format(
                dataset=contract["dataset_name"],
                schema=contract["schema_version"],
                key=contract["key_column"],
                required=len(contract["required_columns"]),
                enums=enum_fields,
                signature=signature_fields,
            )
        )
    lines.extend(["", "## Quality Dimension Coverage", ""])
    for contract in report["contracts"]:
        lines.append(f"### {contract['dataset_name']}")
        for dimension in QUALITY_DIMENSIONS:
            item = contract["quality_dimension_coverage"][dimension]
            lines.append(f"- `{dimension}`: {item['status']} - {item['implementation']}")
        lines.append("")
    lines.extend(["## Boundaries", ""])
    lines.extend(f"- {item}" for item in report["boundaries"])
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
