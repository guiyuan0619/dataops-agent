from __future__ import annotations

from hashlib import sha256

import pandas as pd

from dataops_agent.rules import ALLOWED_DEPARTMENTS, ALLOWED_STATUS
from dataops_agent.schemas import (
    ActionType,
    ChangePreview,
    DataIssue,
    IssueType,
    PreviewResult,
    RepairAction,
    RepairPlan,
)

DEPARTMENT_ALIASES = {
    "cardiology-old": "Cardiology",
    "heart": "Cardiology",
    "neuro": "Neurology",
    "children": "Pediatrics",
    "bone": "Orthopedics",
    "skin": "Dermatology",
}
STATUS_ALIASES = {"enabled": "active", "disabled": "inactive", "archived": "inactive"}
SAFE_MISSING_DEFAULTS = {"title": "Unknown", "source": "manual_review"}


def build_repair_plan(
    df: pd.DataFrame,
    issues: list[DataIssue],
    *,
    dataset_name: str,
) -> RepairPlan:
    actions: list[RepairAction] = []
    for issue in issues:
        action = _action_for_issue(df, issue)
        if action:
            actions.append(action)
    fingerprint = sha256("|".join(action.action_id for action in actions).encode()).hexdigest()[:12]
    return RepairPlan(
        plan_id=f"plan-{dataset_name}-{fingerprint}",
        dataset_name=dataset_name,
        actions=actions,
    )


def preview_plan(df: pd.DataFrame, plan: RepairPlan) -> PreviewResult:
    changes: list[ChangePreview] = []
    for action in plan.actions:
        before = None
        after = action.value
        if action.row_index in df.index and action.field and action.field in df.columns:
            before = str(df.at[action.row_index, action.field])
        changes.append(
            ChangePreview(
                action_id=action.action_id,
                row_index=action.row_index,
                field=action.field,
                before=before,
                after=after,
                action_type=action.action_type,
            )
        )
    return PreviewResult(plan=plan, changes=changes)


def apply_plan(df: pd.DataFrame, plan: RepairPlan) -> pd.DataFrame:
    repaired = df.copy()
    rows_to_drop: list[int] = []
    for action in plan.actions:
        if action.action_type == ActionType.DROP_ROW:
            rows_to_drop.append(action.row_index)
        elif action.action_type == ActionType.SET_VALUE and action.field:
            repaired.at[action.row_index, action.field] = action.value or ""
    if rows_to_drop:
        repaired = repaired.drop(index=rows_to_drop)
    return repaired.reset_index(drop=True)


def _action_for_issue(df: pd.DataFrame, issue: DataIssue) -> RepairAction | None:
    if issue.issue_type == IssueType.DUPLICATE_RECORD:
        return RepairAction(
            action_id=f"drop-duplicate-{issue.row_index}-{issue.field}",
            issue_id=issue.issue_id,
            action_type=ActionType.DROP_ROW,
            row_index=issue.row_index,
            reason=f"Keep the first {issue.field} occurrence and drop duplicate row.",
        )

    if issue.issue_type == IssueType.MISSING_VALUE and issue.field in SAFE_MISSING_DEFAULTS:
        value = SAFE_MISSING_DEFAULTS[issue.field]
        return RepairAction(
            action_id=f"fill-{issue.row_index}-{issue.field}",
            issue_id=issue.issue_id,
            action_type=ActionType.SET_VALUE,
            row_index=issue.row_index,
            field=issue.field,
            value=value,
            reason="Fill safe default for low-risk missing metadata.",
        )

    if issue.issue_type == IssueType.INVALID_ENUM:
        value = str(df.at[issue.row_index, issue.field]).strip().lower()
        mapped = None
        if issue.field == "department":
            mapped = DEPARTMENT_ALIASES.get(value)
            if mapped not in ALLOWED_DEPARTMENTS:
                mapped = None
        elif issue.field == "status":
            mapped = STATUS_ALIASES.get(value)
            if mapped not in ALLOWED_STATUS:
                mapped = None
        if mapped:
            return RepairAction(
                action_id=f"map-{issue.row_index}-{issue.field}",
                issue_id=issue.issue_id,
                action_type=ActionType.SET_VALUE,
                row_index=issue.row_index,
                field=issue.field,
                value=mapped,
                reason="Map known alias into allowed enum.",
            )

    if issue.issue_type == IssueType.STALE_RECORD and "status" in df.columns:
        return RepairAction(
            action_id=f"mark-stale-{issue.row_index}",
            issue_id=issue.issue_id,
            action_type=ActionType.SET_VALUE,
            row_index=issue.row_index,
            field="status",
            value="inactive",
            reason="Mark stale record inactive before publication.",
        )
    return None
