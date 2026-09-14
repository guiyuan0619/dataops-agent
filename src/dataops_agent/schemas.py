from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class IssueType(StrEnum):
    MISSING_VALUE = "missing_value"
    DUPLICATE_RECORD = "duplicate_record"
    INVALID_ENUM = "invalid_enum"
    STALE_RECORD = "stale_record"


class ActionType(StrEnum):
    SET_VALUE = "set_value"
    DROP_ROW = "drop_row"


class DataIssue(BaseModel):
    issue_id: str
    issue_type: IssueType
    row_index: int
    field: str
    message: str
    severity: str = "medium"
    evidence: dict[str, Any] = Field(default_factory=dict)


class DatasetProfile(BaseModel):
    row_count: int
    columns: list[str]
    missing_by_column: dict[str, int]
    duplicate_key_count: int
    invalid_enum_count: int
    stale_row_count: int


class RepairAction(BaseModel):
    action_id: str
    issue_id: str
    action_type: ActionType
    row_index: int
    field: str | None = None
    value: str | None = None
    reason: str


class RepairPlan(BaseModel):
    plan_id: str
    dataset_name: str
    actions: list[RepairAction]
    requires_approval: bool = True


class ChangePreview(BaseModel):
    action_id: str
    row_index: int
    field: str | None
    before: str | None
    after: str | None
    action_type: ActionType


class PreviewResult(BaseModel):
    plan: RepairPlan
    changes: list[ChangePreview]


class VersionRecord(BaseModel):
    version_id: str
    dataset_name: str
    path: str
    checksum: str
    created_at: str
    parent_version_id: str | None = None
    quality_report: dict[str, Any] = Field(default_factory=dict)


class DetectionMetrics(BaseModel):
    true_positive: int
    false_positive: int
    false_negative: int
    precision: float
    recall: float
    f1: float
