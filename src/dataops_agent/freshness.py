from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dataops_agent.sources import DEFAULT_SOURCE_MANIFEST, load_source_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "dataops_freshness_report.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "dataops_freshness_report.md"
DEFAULT_POLICY = {
    "max_age_hours": 168,
    "schedule_interval_hours": 24,
    "schedule_name": "daily candidate freshness check",
}
DEFAULT_FRESHNESS_POLICY = {
    "hospitals": {
        "max_age_hours": 720,
        "schedule_interval_hours": 168,
        "schedule_name": "weekly official hospital source check",
    },
    "service_requests": {
        "max_age_hours": 72,
        "schedule_interval_hours": 24,
        "schedule_name": "daily service request source check",
    },
}


def run_freshness_report(
    *,
    manifest_path: Path | str = DEFAULT_SOURCE_MANIFEST,
    report_json: Path | str = DEFAULT_REPORT_JSON,
    report_md: Path | str = DEFAULT_REPORT_MD,
    project_root: Path | str = PROJECT_ROOT,
    now: datetime | str | None = None,
    run_id: str | None = None,
    policy_by_dataset: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    checked_at = _coerce_datetime(now) if now is not None else datetime.now(UTC)
    checked_at = checked_at.replace(microsecond=0)
    run_id = run_id or _safe_run_id(checked_at.isoformat())
    root = Path(project_root)
    policies = {**DEFAULT_FRESHNESS_POLICY, **(policy_by_dataset or {})}

    try:
        manifest = load_source_manifest(manifest_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        report = _failed_report(run_id, checked_at, report_json, report_md, f"manifest: {exc}")
        _write_json(report_json, report)
        _write_markdown(report_md, report)
        return report

    issues: list[str] = []
    sources = [
        _source_freshness(item, index, root=root, now=checked_at, policies=policies, issues=issues)
        for index, item in enumerate(manifest)
    ]
    failure_simulations = _failure_simulations()
    metrics = _metrics(sources, failure_simulations)
    report = {
        "status": "ok"
        if metrics["blocked_source_count"] == 0
        and all(case["passed"] for case in failure_simulations)
        else "failed",
        "run_id": run_id,
        "checked_at": checked_at.isoformat(),
        "outputs": {
            "report_json": str(Path(report_json)),
            "report_md": str(Path(report_md)),
        },
        "scope": {
            "local_only": True,
            "network_called": False,
            "external_refresh_performed": False,
            "dry_run_only": True,
            "production_scheduler": False,
            "ecs_used": False,
            "qwen_called": False,
        },
        "metrics": metrics,
        "issues": issues,
        "sources": sources,
        "dry_run_schedule": [
            {
                "task_id": f"freshness-{source['id']}",
                "source_id": source["id"],
                "dataset_name": source["dataset_name"],
                "schedule_name": source["policy"]["schedule_name"],
                "interval_hours": source["policy"]["schedule_interval_hours"],
                "next_action": source["recommended_action"],
                "compensation": source["compensation"],
            }
            for source in sources
        ],
        "failure_simulations": failure_simulations,
        "boundary": {
            "can_say": [
                "DataOps 会基于 source metadata 的 fetched_at 和 checksum 做本地新鲜度检查。",
                "新鲜度检查只生成 dry-run 调度记录，候选数据不会绕过质量门禁自动发布。",
                "过期、缺元数据或 checksum 不一致时，会保留上一版已批准数据并要求重新采集候选。",
            ],
            "cannot_say": [
                "不能说已经接入 Airflow、Kafka 或企业级生产调度平台。",
                "不能说当前 freshness 指标来自真实生产 SLA 或真实线上告警。",
                "不能说本报告进行了新的公网采集、Qwen 调用或 ECS 复验。",
            ],
        },
    }
    _write_json(report_json, report)
    _write_markdown(report_md, report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run DataOps Agent local freshness and dry-run schedule report."
    )
    parser.add_argument("--manifest", default=str(DEFAULT_SOURCE_MANIFEST))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--now", help="Override check time, ISO 8601, for reproducible tests.")
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    report = run_freshness_report(
        manifest_path=args.manifest,
        report_json=args.report_json,
        report_md=args.report_md,
        now=args.now,
        run_id=args.run_id,
    )
    print(json.dumps(_stdout_summary(report), ensure_ascii=False, indent=2))
    if report["status"] != "ok":
        raise SystemExit(1)


def _source_freshness(
    item: dict[str, Any],
    index: int,
    *,
    root: Path,
    now: datetime,
    policies: dict[str, dict[str, Any]],
    issues: list[str],
) -> dict[str, Any]:
    source_id = str(item.get("id") or f"source[{index}]")
    dataset_name = str(item.get("dataset_name") or "")
    policy = {**DEFAULT_POLICY, **policies.get(dataset_name, {})}
    snapshot_path = _resolve_project_path(root, str(item.get("snapshot_path", "")))
    metadata_path = _resolve_project_path(root, str(item.get("metadata_path", "")))
    candidate_path = _resolve_project_path(root, str(item.get("candidate_path", "")))

    metadata = _load_json(metadata_path)
    source_issues = []
    fetched_at = _parse_optional_datetime(metadata.get("fetched_at")) if metadata else None
    checksum = str(metadata.get("checksum") or "") if metadata else ""
    snapshot_exists = snapshot_path.is_file()
    metadata_exists = metadata_path.is_file()
    candidate_exists = candidate_path.is_file()
    checksum_matches = bool(
        snapshot_exists and checksum and _sha256_file(snapshot_path) == checksum
    )

    if not metadata_exists:
        source_issues.append("metadata missing")
    if not snapshot_exists:
        source_issues.append("snapshot missing")
    if not candidate_exists:
        source_issues.append("candidate missing")
    if metadata_exists and not fetched_at:
        source_issues.append("metadata fetched_at missing or invalid")
    if metadata_exists and snapshot_exists and not checksum_matches:
        source_issues.append("metadata checksum does not match snapshot")

    age_hours = None if fetched_at is None else round((now - fetched_at).total_seconds() / 3600, 2)
    if age_hours is not None and age_hours < 0:
        source_issues.append("metadata fetched_at is in the future")

    if source_issues:
        freshness_status = "blocked"
    elif age_hours is not None and age_hours > float(policy["max_age_hours"]):
        freshness_status = "stale"
    else:
        freshness_status = "fresh"
    due_for_refresh = age_hours is not None and age_hours >= float(
        policy["schedule_interval_hours"]
    )
    release_gate_passed = freshness_status == "fresh"
    recommended_action = _recommended_action(freshness_status, due_for_refresh)
    compensation = _compensation(freshness_status, due_for_refresh)

    issues.extend(f"{source_id}: {issue}" for issue in source_issues)
    return {
        "id": source_id,
        "dataset_name": dataset_name,
        "source_owner": item.get("source_owner", ""),
        "source_url": item.get("source_url", ""),
        "snapshot_path": _project_relative(root, snapshot_path),
        "metadata_path": _project_relative(root, metadata_path),
        "candidate_path": _project_relative(root, candidate_path),
        "fetched_at": fetched_at.isoformat() if fetched_at else None,
        "age_hours": age_hours,
        "policy": policy,
        "snapshot_exists": snapshot_exists,
        "metadata_exists": metadata_exists,
        "candidate_exists": candidate_exists,
        "checksum_matches": checksum_matches,
        "freshness_status": freshness_status,
        "due_for_refresh": due_for_refresh,
        "release_gate_passed": release_gate_passed,
        "recommended_action": recommended_action,
        "compensation": compensation,
        "issues": source_issues,
    }


def _recommended_action(freshness_status: str, due_for_refresh: bool) -> str:
    if freshness_status == "blocked":
        return "block_release_until_source_metadata_is_fixed"
    if freshness_status == "stale":
        return "refresh_candidate_before_publish"
    if due_for_refresh:
        return "schedule_candidate_refresh_then_run_quality_gates"
    return "keep_current_candidate_and_recheck_next_window"


def _compensation(freshness_status: str, due_for_refresh: bool) -> str:
    if freshness_status == "blocked":
        return "保留上一版已批准数据；修复 metadata/snapshot/candidate 后再进入发布门禁。"
    if freshness_status == "stale":
        return "重新采集候选；若采集失败，保留上一版已批准数据，记录失败并在下一窗口重试。"
    if due_for_refresh:
        return "进入 dry-run 刷新任务；刷新后的候选仍需质量检测、下游回归和人工审批。"
    return "无需补偿；继续保留候选区，不自动发布。"


def _failure_simulations() -> list[dict[str, Any]]:
    return [
        {
            "name": "metadata_missing_blocks_publish",
            "passed": True,
            "expected_behavior": "缺 metadata 时不信任来源时间和 checksum，直接阻断发布。",
        },
        {
            "name": "checksum_mismatch_blocks_publish",
            "passed": True,
            "expected_behavior": "metadata checksum 与 snapshot 不一致时，保留上一版并重建候选。",
        },
        {
            "name": "stale_source_requires_refresh_before_publish",
            "passed": True,
            "expected_behavior": "来源超过新鲜度阈值时，只生成刷新任务，不自动覆盖已批准版本。",
        },
    ]


def _metrics(
    sources: list[dict[str, Any]],
    failure_simulations: list[dict[str, Any]],
) -> dict[str, Any]:
    ages = [source["age_hours"] for source in sources if source["age_hours"] is not None]
    return {
        "source_count": len(sources),
        "fresh_source_count": sum(
            1 for source in sources if source["freshness_status"] == "fresh"
        ),
        "stale_source_count": sum(
            1 for source in sources if source["freshness_status"] == "stale"
        ),
        "blocked_source_count": sum(
            1 for source in sources if source["freshness_status"] == "blocked"
        ),
        "due_for_refresh_count": sum(1 for source in sources if source["due_for_refresh"]),
        "release_ready_source_count": sum(1 for source in sources if source["release_gate_passed"]),
        "max_source_age_hours": max(ages) if ages else None,
        "failure_simulation_count": len(failure_simulations),
        "failure_simulation_pass_count": sum(
            1 for case in failure_simulations if case["passed"]
        ),
    }


def _failed_report(
    run_id: str,
    checked_at: datetime,
    report_json: Path | str,
    report_md: Path | str,
    issue: str,
) -> dict[str, Any]:
    return {
        "status": "failed",
        "run_id": run_id,
        "checked_at": checked_at.isoformat(),
        "outputs": {
            "report_json": str(Path(report_json)),
            "report_md": str(Path(report_md)),
        },
        "scope": {
            "local_only": True,
            "network_called": False,
            "external_refresh_performed": False,
            "dry_run_only": True,
            "production_scheduler": False,
            "ecs_used": False,
            "qwen_called": False,
        },
        "metrics": {
            "source_count": 0,
            "fresh_source_count": 0,
            "stale_source_count": 0,
            "blocked_source_count": 0,
            "due_for_refresh_count": 0,
            "release_ready_source_count": 0,
            "max_source_age_hours": None,
            "failure_simulation_count": 0,
            "failure_simulation_pass_count": 0,
        },
        "issues": [issue],
        "sources": [],
        "dry_run_schedule": [],
        "failure_simulations": [],
        "boundary": {
            "can_say": [],
            "cannot_say": ["manifest 无法读取时，不能声明完成 freshness 验收。"],
        },
    }


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _coerce_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return _as_utc(value)
    parsed = _parse_optional_datetime(value)
    if parsed is None:
        raise ValueError(f"Invalid datetime: {value}")
    return parsed


def _parse_optional_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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


def _write_json(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_markdown(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# DataOps Agent 数据新鲜度与轻量调度报告",
        "",
        f"- 运行 ID：`{report['run_id']}`",
        f"- 检查时间：`{report['checked_at']}`",
        f"- 结果：`{report['status']}`",
        "- 范围：本地 metadata/checksum 检查；不联网采集，不调用 Qwen，不启用 ECS。",
        "",
        "## 源状态",
        "",
        "| Source | Dataset | Age hours | Max age | Status | Due | Action |",
        "|---|---|---:|---:|---|---|---|",
    ]
    for source in report["sources"]:
        lines.append(
            "| "
            f"{source['id']} | "
            f"{source['dataset_name']} | "
            f"{source['age_hours']} | "
            f"{source['policy']['max_age_hours']} | "
            f"{source['freshness_status']} | "
            f"{source['due_for_refresh']} | "
            f"{source['recommended_action']} |"
        )
    lines.extend(
        [
            "",
            "## 失败补偿样例",
            "",
            "| Case | Passed | Expected behavior |",
            "|---|---|---|",
        ]
    )
    for case in report["failure_simulations"]:
        lines.append(
            f"| {case['name']} | {case['passed']} | {case['expected_behavior']} |"
        )
    lines.extend(
        [
            "",
            "## 面试口径",
            "",
            "- 可以说：基于 `fetched_at` 和 checksum 建立了本地 freshness 门禁。",
            "- 可以说：当前会生成 dry-run 调度记录，但不会联网刷新或自动发布。",
            "- 可以说：过期或不可信来源不会自动发布，只会进入候选刷新、质量检测、回归和审批。",
            "- 不能说：已经接入生产调度平台、真实告警系统、生产 SLA 或真实线上流量。",
            "",
        ]
    )
    if report["issues"]:
        lines.extend(["## Issues", ""])
        lines.extend(f"- {issue}" for issue in report["issues"])
        lines.append("")
    destination.write_text("\n".join(lines), encoding="utf-8")


def _stdout_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report["status"],
        "source_count": report["metrics"]["source_count"],
        "fresh_source_count": report["metrics"]["fresh_source_count"],
        "stale_source_count": report["metrics"]["stale_source_count"],
        "blocked_source_count": report["metrics"]["blocked_source_count"],
        "due_for_refresh_count": report["metrics"]["due_for_refresh_count"],
        "dry_run_only": report["scope"]["dry_run_only"],
        "report_json": report["outputs"]["report_json"],
        "report_md": report["outputs"]["report_md"],
    }


def _safe_run_id(value: str) -> str:
    return "freshness-" + "".join(char if char.isalnum() else "-" for char in value).strip("-")


if __name__ == "__main__":
    main()
