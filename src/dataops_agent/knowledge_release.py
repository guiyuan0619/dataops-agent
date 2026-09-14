from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dataops_agent.contracts import dataset_contract, dataset_key
from dataops_agent.rules import load_csv, quality_report
from dataops_agent.sources import PROJECT_ROOT

DEFAULT_OUTPUT_JSONL = PROJECT_ROOT / "reports" / "rag_release_service_requests.jsonl"
DEFAULT_MANIFEST_JSON = PROJECT_ROOT / "reports" / "rag_release_service_requests_manifest.json"


def build_knowledge_documents(
    *,
    dataset_path: Path | str,
    dataset_name: str,
    version_id: str,
) -> list[dict[str, Any]]:
    dataset = dataset_key(dataset_name)
    contract = dataset_contract(dataset)
    df = load_csv(str(dataset_path)).fillna("")
    key_field = contract["key_column"]
    signature_fields = contract["consumer_contract"]["signature_fields"]
    docs = []
    for row_index, row in df.iterrows():
        key_value = str(row.get(key_field, "")).strip()
        if not key_value:
            continue
        if dataset == "doctors" and str(row.get("status", "")).strip() != "active":
            continue
        fields = {
            field: str(row.get(field, "")).strip()
            for field in signature_fields
            if str(row.get(field, "")).strip()
        }
        docs.append(
            {
                "doc_id": f"DATAOPS-{dataset.upper()}-{key_value}",
                "dataset_name": dataset,
                "schema_version": contract["schema_version"],
                "version_id": version_id,
                "source_row": int(row_index) + 2,
                "source_key": key_value,
                "source_url": str(row.get("source_url", "")).strip(),
                "source_checksum": str(row.get("source_checksum", "")).strip(),
                "title": f"{dataset} record {key_value}",
                "content": _content(fields),
                "metadata": fields,
            }
        )
    return docs


def write_knowledge_release(
    *,
    dataset_path: Path | str,
    dataset_name: str,
    version_id: str,
    output_jsonl: Path | str = DEFAULT_OUTPUT_JSONL,
    manifest_json: Path | str = DEFAULT_MANIFEST_JSON,
) -> dict[str, Any]:
    dataset = dataset_key(dataset_name)
    df = load_csv(str(dataset_path))
    report = quality_report(df, dataset_name=dataset)
    if not report["passes_quality_gate"]:
        raise ValueError("Knowledge release requires a dataset that passes the quality gate.")

    docs = build_knowledge_documents(
        dataset_path=dataset_path,
        dataset_name=dataset,
        version_id=version_id,
    )
    output_path = Path(output_jsonl)
    manifest_path = Path(manifest_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(doc, ensure_ascii=False, sort_keys=True) for doc in docs)
    output_path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")

    manifest = {
        "status": "ok",
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_name": dataset,
        "schema_version": dataset_contract(dataset)["schema_version"],
        "version_id": version_id,
        "dataset_path": str(dataset_path),
        "knowledge_jsonl": str(output_path),
        "doc_count": len(docs),
        "source_url_count": len({doc["source_url"] for doc in docs if doc["source_url"]}),
        "source_checksum_count": len(
            {doc["source_checksum"] for doc in docs if doc["source_checksum"]}
        ),
        "quality_report": report,
        "rag_boundary": {
            "dataops_exports_docs": True,
            "dataops_builds_embedding": False,
            "dataops_writes_vector_db": False,
            "downstream_should_reindex_after_approval": True,
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Export an approved dataset as RAG-ready JSONL.")
    parser.add_argument(
        "--dataset-path",
        default=str(PROJECT_ROOT / "data" / "imported" / "nyc_311_service_requests_sample.csv"),
    )
    parser.add_argument("--dataset-name", default="service_requests")
    parser.add_argument("--version-id", default="service-requests-sample-v1")
    parser.add_argument("--output-jsonl", default=str(DEFAULT_OUTPUT_JSONL))
    parser.add_argument("--manifest-json", default=str(DEFAULT_MANIFEST_JSON))
    args = parser.parse_args(argv)
    manifest = write_knowledge_release(
        dataset_path=args.dataset_path,
        dataset_name=args.dataset_name,
        version_id=args.version_id,
        output_jsonl=args.output_jsonl,
        manifest_json=args.manifest_json,
    )
    print(json.dumps(_stdout_summary(manifest), ensure_ascii=False, indent=2))


def _content(fields: dict[str, str]) -> str:
    return "; ".join(f"{field}: {value}" for field, value in fields.items())


def _stdout_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": manifest["status"],
        "dataset_name": manifest["dataset_name"],
        "version_id": manifest["version_id"],
        "doc_count": manifest["doc_count"],
        "knowledge_jsonl": manifest["knowledge_jsonl"],
    }


if __name__ == "__main__":
    main()
