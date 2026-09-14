from __future__ import annotations

from typing import Any

import pandas as pd

CONSUMER_KEY_FIELDS = {
    "doctors": "doctor_id",
    "hospitals": "hospital_id",
    "service_requests": "request_id",
}
CONSUMER_SIGNATURE_FIELDS = {
    "doctors": ("name", "department", "title", "status"),
    "hospitals": ("name", "city", "state", "hospital_type", "emergency_services", "rating"),
    "service_requests": ("agency", "complaint_type", "status", "borough"),
}


def evaluate_consumer_gate(
    *,
    dataset_name: str,
    candidate: pd.DataFrame,
    quality_report: dict[str, Any],
    baseline: pd.DataFrame | None = None,
) -> dict[str, Any]:
    candidate_cases = _consumer_cases(candidate, dataset_name=dataset_name, visible_only=True)
    candidate_all_cases = _consumer_cases(candidate, dataset_name=dataset_name, visible_only=False)
    baseline_cases = (
        _consumer_cases(baseline, dataset_name=dataset_name, visible_only=True)
        if baseline is not None
        else {}
    )
    missing_baseline_keys = sorted(set(baseline_cases) - set(candidate_all_cases))
    changed_keys = sorted(
        key
        for key in set(baseline_cases) & set(candidate_all_cases)
        if baseline_cases[key] != candidate_all_cases[key]
    )
    candidate_pass_count = sum(1 for values in candidate_cases.values() if all(values))
    candidate_case_count = len(candidate_cases)
    baseline_key_coverage = (
        None
        if not baseline_cases
        else _safe_ratio(
            len(baseline_cases) - len(missing_baseline_keys),
            len(baseline_cases),
        )
    )

    if not quality_report.get("passes_quality_gate", False):
        status = "blocked_quality_gate"
        recommended_next_step = "start governance flow before hospital regression"
    elif not candidate_cases:
        status = "failed_regression"
        recommended_next_step = "add consumer-visible rows before publishing"
    elif missing_baseline_keys:
        status = "failed_regression"
        recommended_next_step = "review missing baseline records before publishing"
    else:
        status = "passed"
        recommended_next_step = "request human approval before publishing this candidate"

    return {
        "status": status,
        "release_gate_passed": status == "passed",
        "recommended_next_step": recommended_next_step,
        "metrics": {
            "candidate_case_count": candidate_case_count,
            "candidate_canary_pass_rate": _safe_ratio(
                candidate_pass_count,
                candidate_case_count,
            ),
            "baseline_case_count": len(baseline_cases),
            "baseline_key_coverage": baseline_key_coverage,
            "missing_baseline_key_count": len(missing_baseline_keys),
            "changed_case_count": len(changed_keys),
            "quality_issue_count": int(quality_report.get("issue_count", 0)),
        },
        "failed_cases": [
            {"case_key": key, "reason": "missing_from_candidate"}
            for key in missing_baseline_keys[:20]
        ],
        "changed_cases": [
            {"case_key": key, "reason": "consumer_signature_changed"}
            for key in changed_keys[:20]
        ],
    }


def _consumer_cases(
    df: pd.DataFrame,
    *,
    dataset_name: str,
    visible_only: bool,
) -> dict[str, tuple[str, ...]]:
    key_field = CONSUMER_KEY_FIELDS.get(dataset_name)
    signature_fields = CONSUMER_SIGNATURE_FIELDS.get(dataset_name, ())
    if not key_field or key_field not in df.columns:
        return {}
    working = df.fillna("")
    if visible_only and dataset_name == "doctors" and "status" in working.columns:
        working = working[working["status"].astype(str).str.strip() == "active"]

    cases: dict[str, tuple[str, ...]] = {}
    for _, row in working.iterrows():
        case_key = str(row.get(key_field, "")).strip()
        if case_key:
            cases[case_key] = tuple(
                str(row.get(field, "")).strip() for field in signature_fields
            )
    return cases


def _safe_ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else round(numerator / denominator, 4)
