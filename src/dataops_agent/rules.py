from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd

from dataops_agent.schemas import DataIssue, DatasetProfile, IssueType

REQUIRED_COLUMNS = [
    "doctor_id",
    "name",
    "department",
    "title",
    "status",
    "updated_at",
    "source",
]
ALLOWED_DEPARTMENTS = {"Cardiology", "Neurology", "Pediatrics", "Orthopedics", "Dermatology"}
ALLOWED_STATUS = {"active", "inactive"}
CMS_HOSPITAL_REQUIRED_COLUMNS = [
    "hospital_id",
    "name",
    "city",
    "state",
    "hospital_type",
    "emergency_services",
    "rating",
    "source_url",
    "source_checksum",
]
SERVICE_REQUEST_REQUIRED_COLUMNS = [
    "request_id",
    "created_at",
    "agency",
    "complaint_type",
    "status",
    "borough",
    "source_url",
    "source_checksum",
]
ALLOWED_EMERGENCY_SERVICES = {"Yes", "No"}
ALLOWED_CMS_RATINGS = {"1", "2", "3", "4", "5", "Not Available"}
ALLOWED_SERVICE_REQUEST_STATUS = {"Open", "In Progress", "Pending", "Started", "Assigned", "Closed"}
ALLOWED_NYC_BOROUGHS = {"BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND", "Unspecified"}
STALE_AFTER_DAYS = 365
DATASET_ALIASES = {
    "cms_hospitals": "hospitals",
    "hospital_general_information": "hospitals",
    "nyc_311": "service_requests",
    "nyc_311_service_requests": "service_requests",
}
DATASET_SCHEMA_VERSIONS = {
    "doctors": "doctors_v1",
    "hospitals": "hospitals_v1",
    "service_requests": "service_requests_v1",
}
REQUIRED_COLUMNS_BY_DATASET = {
    "doctors": REQUIRED_COLUMNS,
    "hospitals": CMS_HOSPITAL_REQUIRED_COLUMNS,
    "service_requests": SERVICE_REQUEST_REQUIRED_COLUMNS,
}
KEY_COLUMN_BY_DATASET = {
    "doctors": "doctor_id",
    "hospitals": "hospital_id",
    "service_requests": "request_id",
}
ENUM_RULES_BY_DATASET = {
    "doctors": {"department": ALLOWED_DEPARTMENTS, "status": ALLOWED_STATUS},
    "hospitals": {
        "emergency_services": ALLOWED_EMERGENCY_SERVICES,
        "rating": ALLOWED_CMS_RATINGS,
    },
    "service_requests": {
        "status": ALLOWED_SERVICE_REQUEST_STATUS,
        "borough": ALLOWED_NYC_BOROUGHS,
    },
}


def load_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def profile_dataframe(
    df: pd.DataFrame,
    *,
    dataset_name: str = "doctors",
    as_of: date | None = None,
) -> DatasetProfile:
    as_of = as_of or date.today()
    dataset = _dataset_key(dataset_name)
    key_column = KEY_COLUMN_BY_DATASET[dataset]
    missing = {column: _missing_mask(df[column]).sum().item() for column in df.columns}
    duplicate_count = (
        _duplicate_mask(df, key_column).sum().item() if key_column in df.columns else 0
    )
    invalid_enum_count = _invalid_enum_mask(df, dataset).sum().item()
    stale_count = (
        _stale_mask(df, as_of=as_of).sum().item()
        if dataset == "doctors" and "updated_at" in df.columns
        else 0
    )
    return DatasetProfile(
        row_count=len(df),
        columns=list(df.columns),
        missing_by_column={key: int(value) for key, value in missing.items()},
        duplicate_key_count=int(duplicate_count),
        invalid_enum_count=int(invalid_enum_count),
        stale_row_count=int(stale_count),
    )


def detect_issues(
    df: pd.DataFrame,
    *,
    dataset_name: str = "doctors",
    as_of: date | None = None,
) -> list[DataIssue]:
    as_of = as_of or date.today()
    dataset = _dataset_key(dataset_name)
    required_columns = REQUIRED_COLUMNS_BY_DATASET[dataset]
    key_column = KEY_COLUMN_BY_DATASET[dataset]
    issues: list[DataIssue] = []
    for column in required_columns:
        if column not in df.columns:
            issues.append(
                DataIssue(
                    issue_id=f"missing-column:{column}",
                    issue_type=IssueType.MISSING_VALUE,
                    row_index=-1,
                    field=column,
                    message=f"Required column {column} is missing.",
                    severity="high",
                )
            )
            continue
        for row_index in df.index[_missing_mask(df[column])].tolist():
            issues.append(
                DataIssue(
                    issue_id=f"missing:{row_index}:{column}",
                    issue_type=IssueType.MISSING_VALUE,
                    row_index=int(row_index),
                    field=column,
                    message=f"Required field {column} is empty.",
                    severity="high" if column in {key_column, "name"} else "medium",
                )
            )

    if key_column in df.columns:
        duplicate_mask = _duplicate_mask(df, key_column)
        for row_index in df.index[duplicate_mask].tolist():
            issues.append(
                DataIssue(
                    issue_id=f"duplicate:{row_index}:{key_column}",
                    issue_type=IssueType.DUPLICATE_RECORD,
                    row_index=int(row_index),
                    field=key_column,
                    message=f"Duplicate {key_column} detected.",
                    severity="high",
                    evidence={key_column: str(df.at[row_index, key_column])},
                )
            )

    for field, allowed_values in ENUM_RULES_BY_DATASET[dataset].items():
        if field not in df.columns:
            continue
        invalid_mask = ~df[field].isin(allowed_values) & ~_missing_mask(df[field])
        for row_index in df.index[invalid_mask].tolist():
            value = str(df.at[row_index, field])
            issues.append(
                DataIssue(
                    issue_id=f"invalid-enum:{row_index}:{field}",
                    issue_type=IssueType.INVALID_ENUM,
                    row_index=int(row_index),
                    field=field,
                    message=f"{field} is outside the allowed enum.",
                    evidence={"value": value, "allowed": sorted(allowed_values)},
                )
            )

    if dataset == "doctors" and "updated_at" in df.columns:
        for row_index in df.index[_stale_mask(df, as_of=as_of)].tolist():
            issues.append(
                DataIssue(
                    issue_id=f"stale:{row_index}:updated_at",
                    issue_type=IssueType.STALE_RECORD,
                    row_index=int(row_index),
                    field="updated_at",
                    message=f"Record is older than {STALE_AFTER_DAYS} days.",
                    severity="medium",
                    evidence={"updated_at": str(df.at[row_index, "updated_at"])},
                )
            )
    return sorted(issues, key=lambda item: item.issue_id)


def quality_report(
    df: pd.DataFrame,
    *,
    dataset_name: str = "doctors",
    as_of: date | None = None,
) -> dict[str, Any]:
    dataset = _dataset_key(dataset_name)
    issues = detect_issues(df, dataset_name=dataset, as_of=as_of)
    profile = profile_dataframe(df, dataset_name=dataset, as_of=as_of)
    by_type: dict[str, int] = {}
    for issue in issues:
        by_type[issue.issue_type.value] = by_type.get(issue.issue_type.value, 0) + 1
    return {
        "dataset_name": dataset,
        "profile": profile.model_dump(),
        "issue_count": len(issues),
        "issues_by_type": by_type,
        "passes_quality_gate": len(issues) == 0,
    }


def _missing_mask(series: pd.Series) -> pd.Series:
    return series.isna() | (series.astype(str).str.strip() == "")


def _duplicate_mask(df: pd.DataFrame, key_column: str) -> pd.Series:
    present = ~_missing_mask(df[key_column])
    return df[key_column].duplicated(keep="first") & present


def _invalid_enum_mask(df: pd.DataFrame, dataset: str) -> pd.Series:
    masks: list[pd.Series] = []
    for field, allowed_values in ENUM_RULES_BY_DATASET[dataset].items():
        if field in df.columns:
            masks.append(~df[field].isin(allowed_values) & ~_missing_mask(df[field]))
    if not masks:
        return pd.Series(False, index=df.index)
    result = masks[0]
    for mask in masks[1:]:
        result = result | mask
    return result


def _stale_mask(df: pd.DataFrame, *, as_of: date) -> pd.Series:
    def is_stale(value: str) -> bool:
        try:
            parsed = datetime.strptime(str(value), "%Y-%m-%d").date()
        except ValueError:
            return False
        return (as_of - parsed).days > STALE_AFTER_DAYS

    stale = df["updated_at"].astype(str).map(is_stale)
    if "status" not in df.columns:
        return stale
    return stale & (df["status"] == "active")


def _dataset_key(dataset_name: str) -> str:
    normalized = DATASET_ALIASES.get(dataset_name, dataset_name)
    if normalized not in REQUIRED_COLUMNS_BY_DATASET:
        return "doctors"
    return normalized
