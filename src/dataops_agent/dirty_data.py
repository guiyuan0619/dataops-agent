from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def inject_dirty_data(
    input_csv: str | Path,
    output_csv: str | Path,
    labels_json: str | Path,
    *,
    dataset_name: str = "doctors",
) -> None:
    if dataset_name in {"hospitals", "cms_hospitals", "hospital_general_information"}:
        inject_dirty_hospital_data(input_csv, output_csv, labels_json)
        return
    if dataset_name in {"service_requests", "nyc_311", "nyc_311_service_requests"}:
        inject_dirty_service_request_data(input_csv, output_csv, labels_json)
        return
    inject_dirty_doctor_data(input_csv, output_csv, labels_json)


def inject_dirty_doctor_data(
    input_csv: str | Path,
    output_csv: str | Path,
    labels_json: str | Path,
) -> None:
    df = pd.read_csv(input_csv, dtype=str, keep_default_na=False)
    if len(df) < 6:
        raise ValueError("Dirty data injector expects at least 6 rows.")

    df.at[1, "title"] = ""
    df.at[2, "department"] = "cardiology-old"
    df.at[3, "updated_at"] = "2024-01-01"
    df.at[4, "source"] = ""
    duplicate = df.iloc[[0]].copy()
    df = pd.concat([df, duplicate], ignore_index=True)

    labels = {
        "issues": [
            {"issue_type": "missing_value", "row_index": 1, "field": "title"},
            {"issue_type": "invalid_enum", "row_index": 2, "field": "department"},
            {"issue_type": "stale_record", "row_index": 3, "field": "updated_at"},
            {"issue_type": "missing_value", "row_index": 4, "field": "source"},
            {"issue_type": "duplicate_record", "row_index": 6, "field": "doctor_id"},
        ]
    }

    output_csv = Path(output_csv)
    labels_json = Path(labels_json)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    labels_json.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False)
    labels_json.write_text(json.dumps(labels, indent=2), encoding="utf-8")


def inject_dirty_hospital_data(
    input_csv: str | Path,
    output_csv: str | Path,
    labels_json: str | Path,
) -> None:
    df = pd.read_csv(input_csv, dtype=str, keep_default_na=False)
    if len(df) < 5:
        raise ValueError("Dirty hospital data injector expects at least 5 rows.")

    df.at[1, "name"] = ""
    df.at[2, "emergency_services"] = "Maybe"
    df.at[3, "rating"] = "9"
    df.at[4, "source_url"] = ""
    duplicate_row_index = len(df)
    duplicate = df.iloc[[0]].copy()
    df = pd.concat([df, duplicate], ignore_index=True)

    labels = {
        "dataset_name": "hospitals",
        "issues": [
            {"issue_type": "missing_value", "row_index": 1, "field": "name"},
            {"issue_type": "invalid_enum", "row_index": 2, "field": "emergency_services"},
            {"issue_type": "invalid_enum", "row_index": 3, "field": "rating"},
            {"issue_type": "missing_value", "row_index": 4, "field": "source_url"},
            {
                "issue_type": "duplicate_record",
                "row_index": duplicate_row_index,
                "field": "hospital_id",
            },
        ],
    }

    output_csv = Path(output_csv)
    labels_json = Path(labels_json)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    labels_json.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False)
    labels_json.write_text(json.dumps(labels, indent=2), encoding="utf-8")


def inject_dirty_service_request_data(
    input_csv: str | Path,
    output_csv: str | Path,
    labels_json: str | Path,
) -> None:
    df = pd.read_csv(input_csv, dtype=str, keep_default_na=False)
    if len(df) < 5:
        raise ValueError("Dirty service request injector expects at least 5 rows.")

    df.at[1, "complaint_type"] = ""
    df.at[2, "status"] = "Maybe"
    df.at[3, "borough"] = "Atlantis"
    df.at[4, "source_url"] = ""
    duplicate_row_index = len(df)
    duplicate = df.iloc[[0]].copy()
    df = pd.concat([df, duplicate], ignore_index=True)

    labels = {
        "dataset_name": "service_requests",
        "issues": [
            {"issue_type": "missing_value", "row_index": 1, "field": "complaint_type"},
            {"issue_type": "invalid_enum", "row_index": 2, "field": "status"},
            {"issue_type": "invalid_enum", "row_index": 3, "field": "borough"},
            {"issue_type": "missing_value", "row_index": 4, "field": "source_url"},
            {
                "issue_type": "duplicate_record",
                "row_index": duplicate_row_index,
                "field": "request_id",
            },
        ],
    }

    output_csv = Path(output_csv)
    labels_json = Path(labels_json)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    labels_json.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False)
    labels_json.write_text(json.dumps(labels, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-name", default="doctors")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--labels", required=True)
    args = parser.parse_args()
    inject_dirty_data(args.input, args.output, args.labels, dataset_name=args.dataset_name)


if __name__ == "__main__":
    main()
