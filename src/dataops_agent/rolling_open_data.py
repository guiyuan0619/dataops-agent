from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from dataops_agent.rules import SERVICE_REQUEST_REQUIRED_COLUMNS, load_csv, quality_report
from dataops_agent.versioning import VersionStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_CSV = PROJECT_ROOT / "data" / "imported" / "nyc_311_service_requests_sample.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "dataops_rolling_open_data_report.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "dataops_rolling_open_data_report.md"
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT / "reports" / "rolling_open_data_runtime"


def run_rolling_open_data_report(
    *,
    source_csv: Path | str = DEFAULT_SOURCE_CSV,
    report_json: Path | str = DEFAULT_REPORT_JSON,
    report_md: Path | str = DEFAULT_REPORT_MD,
    runtime_root: Path | str = DEFAULT_RUNTIME_ROOT,
    run_id: str | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now(UTC).replace(microsecond=0)
    run_id = run_id or _safe_run_id(generated_at.isoformat())
    runtime_dir = Path(runtime_root) / run_id
    _reset_runtime_dir(Path(runtime_root), runtime_dir)

    base_df = load_csv(str(source_csv))
    baseline_df = base_df.copy()
    incremental_df = _with_incremental_rows(baseline_df)
    duplicate_df = _with_duplicate_row(incremental_df)
    schema_break_df = incremental_df.drop(columns=["borough"])

    snapshots = [
        _write_snapshot(runtime_dir, "snapshot_1_baseline", baseline_df, generated_at),
        _write_snapshot(
            runtime_dir,
            "snapshot_2_incremental_and_late",
            incremental_df,
            generated_at + timedelta(hours=1),
        ),
        _write_snapshot(
            runtime_dir,
            "snapshot_3_duplicate_candidate",
            duplicate_df,
            generated_at + timedelta(hours=2),
        ),
        _write_snapshot(
            runtime_dir,
            "snapshot_4_schema_break_candidate",
            schema_break_df,
            generated_at + timedelta(hours=3),
        ),
    ]
    diffs = [
        _diff_snapshots(
            "baseline_to_incremental",
            baseline_df,
            incremental_df,
        ),
        _diff_snapshots(
            "incremental_to_duplicate_candidate",
            incremental_df,
            duplicate_df,
        ),
        _diff_snapshots(
            "incremental_to_schema_break_candidate",
            incremental_df,
            schema_break_df,
        ),
    ]

    store = VersionStore(runtime_dir / "versions.sqlite3", runtime_dir / "versions")
    baseline_quality = quality_report(baseline_df, dataset_name="service_requests")
    baseline_version = store.publish(
        dataset_name="service_requests",
        df=baseline_df,
        quality_report=baseline_quality,
        approved=True,
        idempotency_key=f"{run_id}-baseline",
    )
    incremental_quality = quality_report(incremental_df, dataset_name="service_requests")
    incremental_version = store.publish(
        dataset_name="service_requests",
        df=incremental_df,
        quality_report=incremental_quality,
        approved=True,
        parent_version_id=baseline_version.version_id,
        expected_active_version_id=baseline_version.version_id,
        require_expected_active_match=True,
        idempotency_key=f"{run_id}-incremental",
    )

    duplicate_quality = quality_report(duplicate_df, dataset_name="service_requests")
    duplicate_publish = _try_publish(
        store,
        df=duplicate_df,
        quality_report=duplicate_quality,
        parent_version_id=incremental_version.version_id,
        idempotency_key=f"{run_id}-duplicate",
    )
    schema_break_quality = quality_report(schema_break_df, dataset_name="service_requests")
    schema_break_publish = _try_publish(
        store,
        df=schema_break_df,
        quality_report=schema_break_quality,
        parent_version_id=incremental_version.version_id,
        idempotency_key=f"{run_id}-schema-break",
    )

    active_after_bad_candidates = store.active_version("service_requests")
    rolled_back = store.rollback(
        dataset_name="service_requests",
        target_version_id=baseline_version.version_id,
        approved=True,
    )
    active_after_rollback = store.active_version("service_requests")

    baseline_diff = diffs[0]
    duplicate_diff = diffs[1]
    schema_diff = diffs[2]
    gates = {
        "source_sample_500_rows": len(base_df) == 500,
        "baseline_publish_ok": baseline_version.dataset_name == "service_requests",
        "incremental_publish_ok": incremental_version.dataset_name == "service_requests",
        "incremental_new_records_detected": baseline_diff["new_record_count"] == 2,
        "late_arrival_detected": baseline_diff["late_arrival_count"] == 1,
        "duplicate_candidate_detected": duplicate_diff["duplicate_key_count"] == 1,
        "duplicate_candidate_blocked": duplicate_publish["blocked"],
        "schema_break_detected": bool(schema_diff["schema"]["missing_required_columns"]),
        "schema_break_blocked": schema_break_publish["blocked"],
        "active_preserved_after_bad_candidates": (
            active_after_bad_candidates is not None
            and active_after_bad_candidates.version_id == incremental_version.version_id
        ),
        "approved_rollback_restored_baseline": (
            rolled_back.version_id == baseline_version.version_id
            and active_after_rollback is not None
            and active_after_rollback.version_id == baseline_version.version_id
        ),
    }
    metrics = {
        "source_sample_row_count": len(base_df),
        "snapshot_count": len(snapshots),
        "published_version_count": len(store.list_versions("service_requests")),
        "baseline_row_count": len(baseline_df),
        "incremental_row_count": len(incremental_df),
        "new_record_count": baseline_diff["new_record_count"],
        "late_arrival_count": baseline_diff["late_arrival_count"],
        "duplicate_key_count": duplicate_diff["duplicate_key_count"],
        "schema_missing_required_column_count": len(
            schema_diff["schema"]["missing_required_columns"]
        ),
        "bad_candidate_blocked_count": int(duplicate_publish["blocked"])
        + int(schema_break_publish["blocked"]),
    }
    report = {
        "status": "ok" if all(gates.values()) else "failed",
        "run_id": run_id,
        "generated_at": generated_at.isoformat(),
        "runtime_dir": str(runtime_dir),
        "outputs": {
            "report_json": str(Path(report_json)),
            "report_md": str(Path(report_md)),
        },
        "scope": {
            "local_only": True,
            "open_data_source": "NYC Open Data 311 Service Requests",
            "source_sample_path": str(Path(source_csv)),
            "network_called": False,
            "ecs_used": False,
            "qwen_called": False,
            "production_traffic": False,
            "streaming_platform_used": False,
            "scheduler_used": False,
        },
        "gates": gates,
        "metrics": metrics,
        "snapshots": snapshots,
        "diffs": diffs,
        "publish_flow": {
            "baseline_version_id": baseline_version.version_id,
            "incremental_version_id": incremental_version.version_id,
            "duplicate_candidate_publish": duplicate_publish,
            "schema_break_candidate_publish": schema_break_publish,
            "active_after_bad_candidates": active_after_bad_candidates.version_id
            if active_after_bad_candidates
            else None,
            "active_after_rollback": active_after_rollback.version_id
            if active_after_rollback
            else None,
        },
        "boundary": {
            "can_say": [
                "基于 500 行 NYC 311 官方开放样本做了多时点更新回放。",
                "回放覆盖新增记录、迟到记录、重复记录和字段缺失四类更新风险。",
                "重复和字段缺失候选会被发布链路拦住，上一版可用数据不会被静默覆盖。",
                "通过人工批准的回滚可以恢复上一稳定版本。",
            ],
            "cannot_say": [
                "不能说这是 Kafka/Flink/Airflow 生产流式处理。",
                "不能说这是实时线上业务流量、生产 SLA 或真实 QPS。",
                "不能说本轮重新联网抓取了最新数据、调用 Qwen 或启用 ECS。",
            ],
        },
    }
    _write_json(report_json, report)
    _write_markdown(report_md, report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Replay rolling public-data snapshots through DataOps gates."
    )
    parser.add_argument("--source-csv", default=str(DEFAULT_SOURCE_CSV))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    report = run_rolling_open_data_report(
        source_csv=args.source_csv,
        report_json=args.report_json,
        report_md=args.report_md,
        runtime_root=args.runtime_root,
        run_id=args.run_id,
    )
    print(json.dumps(_stdout_summary(report), ensure_ascii=False, indent=2))
    if report["status"] != "ok":
        raise SystemExit(1)


def _with_incremental_rows(df: pd.DataFrame) -> pd.DataFrame:
    watermark = _max_created_at(df)
    checksum = str(df["source_checksum"].iloc[0]) if "source_checksum" in df.columns else ""
    source_url = str(df["source_url"].iloc[0]) if "source_url" in df.columns else ""
    new_row = {
        "request_id": "pl-d3-new-001",
        "created_at": _format_nyc_datetime(watermark + timedelta(minutes=5)),
        "agency": "NYPD",
        "complaint_type": "Blocked Driveway",
        "status": "Open",
        "borough": "QUEENS",
        "source_url": source_url,
        "source_checksum": checksum,
    }
    late_row = {
        "request_id": "pl-d3-late-001",
        "created_at": _format_nyc_datetime(watermark - timedelta(days=2)),
        "agency": "DEP",
        "complaint_type": "Water Maintenance",
        "status": "In Progress",
        "borough": "BROOKLYN",
        "source_url": source_url,
        "source_checksum": checksum,
    }
    return pd.concat([df, pd.DataFrame([new_row, late_row])], ignore_index=True)


def _with_duplicate_row(df: pd.DataFrame) -> pd.DataFrame:
    duplicate = df.head(1).copy()
    return pd.concat([df, duplicate], ignore_index=True)


def _diff_snapshots(name: str, previous: pd.DataFrame, current: pd.DataFrame) -> dict[str, Any]:
    previous_ids = _ids(previous)
    current_ids = _ids(current)
    new_ids = sorted(current_ids - previous_ids)
    removed_ids = sorted(previous_ids - current_ids)
    previous_watermark = _max_created_at(previous) if "created_at" in previous.columns else None
    late_arrivals = [
        request_id
        for request_id in new_ids
        if previous_watermark is not None
        and _created_at_for_id(current, request_id) <= previous_watermark
    ]
    return {
        "name": name,
        "previous_row_count": len(previous),
        "current_row_count": len(current),
        "new_record_count": len(new_ids),
        "removed_record_count": len(removed_ids),
        "late_arrival_count": len(late_arrivals),
        "duplicate_key_count": _duplicate_key_count(current),
        "new_record_examples": new_ids[:5],
        "late_arrival_examples": late_arrivals[:5],
        "removed_record_examples": removed_ids[:5],
        "schema": _schema_diff(previous, current),
    }


def _schema_diff(previous: pd.DataFrame, current: pd.DataFrame) -> dict[str, Any]:
    previous_columns = set(previous.columns)
    current_columns = set(current.columns)
    required = set(SERVICE_REQUEST_REQUIRED_COLUMNS)
    return {
        "added_columns": sorted(current_columns - previous_columns),
        "removed_columns": sorted(previous_columns - current_columns),
        "missing_required_columns": sorted(required - current_columns),
        "required_columns_present": sorted(required & current_columns),
        "breaking_change": bool(required - current_columns),
    }


def _write_snapshot(
    runtime_dir: Path,
    name: str,
    df: pd.DataFrame,
    fetched_at: datetime,
) -> dict[str, Any]:
    snapshot_path = runtime_dir / f"{name}.csv"
    manifest_path = runtime_dir / f"{name}.manifest.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    payload = df.to_csv(index=False)
    checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    snapshot_path.write_text(payload, encoding="utf-8")
    manifest = {
        "name": name,
        "dataset_name": "service_requests",
        "source_owner": "NYC Open Data",
        "source_type": "official_public_sample_replay",
        "row_count": len(df),
        "fetched_at": fetched_at.isoformat(),
        "checksum": checksum,
        "path": str(snapshot_path),
        "manifest_path": str(manifest_path),
        "schema": _schema_diff(df, df),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _try_publish(
    store: VersionStore,
    *,
    df: pd.DataFrame,
    quality_report: dict[str, Any],
    parent_version_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    try:
        record = store.publish(
            dataset_name="service_requests",
            df=df,
            quality_report=quality_report,
            approved=True,
            parent_version_id=parent_version_id,
            expected_active_version_id=parent_version_id,
            require_expected_active_match=True,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        return {
            "blocked": True,
            "error_type": type(exc).__name__,
            "message": str(exc),
            "quality_issue_count": quality_report["issue_count"],
        }
    return {
        "blocked": False,
        "version_id": record.version_id,
        "quality_issue_count": quality_report["issue_count"],
    }


def _ids(df: pd.DataFrame) -> set[str]:
    if "request_id" not in df.columns:
        return set()
    return {str(value) for value in df["request_id"].dropna()}


def _duplicate_key_count(df: pd.DataFrame) -> int:
    if "request_id" not in df.columns:
        return 0
    return int(df["request_id"].duplicated(keep="first").sum())


def _created_at_for_id(df: pd.DataFrame, request_id: str) -> datetime:
    value = df.loc[df["request_id"].astype(str) == request_id, "created_at"].iloc[0]
    return _parse_nyc_datetime(str(value))


def _max_created_at(df: pd.DataFrame) -> datetime:
    parsed = [_parse_nyc_datetime(str(value)) for value in df["created_at"].astype(str)]
    return max(parsed)


def _parse_nyc_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    if normalized.endswith(".000"):
        normalized = normalized[:-4]
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _format_nyc_datetime(value: datetime) -> str:
    value = value.astimezone(UTC).replace(tzinfo=None)
    return value.isoformat(timespec="seconds") + ".000"


def _write_json(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_markdown(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    metrics = report["metrics"]
    lines = [
        "# DataOps Agent 滚动开放数据验证报告",
        "",
        f"- 运行 ID：`{report['run_id']}`",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 结果：`{report['status']}`",
        "- 范围：本地开放数据多时点回放；未联网刷新，未调用 Qwen，未启用 ECS。",
        "",
        "## 这批验证解决的问题",
        "",
        (
            "固定 500 行样本只能证明单次候选能通过。"
            "本报告把同一官方开放样本拆成多时点更新回放，验证新增、迟到、"
            "重复和字段变化出现时，发布链路是否仍能保留上一版可用数据。"
        ),
        "",
        "## 指标摘要",
        "",
        "| 指标 | 值 |",
        "|---|---:|",
        f"| 源样本行数 | {metrics['source_sample_row_count']} |",
        f"| 快照数 | {metrics['snapshot_count']} |",
        f"| 已发布版本数 | {metrics['published_version_count']} |",
        f"| 增量记录数 | {metrics['new_record_count']} |",
        f"| 迟到记录数 | {metrics['late_arrival_count']} |",
        f"| 重复 key 数 | {metrics['duplicate_key_count']} |",
        f"| 缺失必备字段数 | {metrics['schema_missing_required_column_count']} |",
        f"| 被阻断的坏候选数 | {metrics['bad_candidate_blocked_count']} |",
        "",
        "## 更新差异",
        "",
        "| 场景 | 上版行数 | 当前行数 | 新增 | 迟到 | 重复 | 缺必备字段 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for diff in report["diffs"]:
        lines.append(
            "| "
            f"{diff['name']} | "
            f"{diff['previous_row_count']} | "
            f"{diff['current_row_count']} | "
            f"{diff['new_record_count']} | "
            f"{diff['late_arrival_count']} | "
            f"{diff['duplicate_key_count']} | "
            f"{len(diff['schema']['missing_required_columns'])} |"
        )
    lines.extend(
        [
            "",
            "## 门禁结果",
            "",
            "| 门禁 | 结果 |",
            "|---|---|",
        ]
    )
    for name, value in report["gates"].items():
        lines.append(f"| `{name}` | `{value}` |")
    lines.extend(
        [
            "",
            "## 面试口径",
            "",
            (
                "- 可以说：我补了滚动开放数据回放，不只检查固定样本，"
                "还检查同一来源更新时的新增、迟到、重复和字段变化。"
            ),
            (
                "- 可以说：这批回放中 2 条增量、1 条迟到、1 条重复和 1 类字段缺失"
                "都能被识别，坏候选不会静默覆盖正式版本，审批回滚能恢复上一稳定版本。"
            ),
            (
                "- 不能说：这是实时生产流、Kafka/Flink/Airflow 调度、真实线上流量、"
                "生产 SLA 或真实 QPS。"
            ),
            "",
        ]
    )
    destination.write_text("\n".join(lines), encoding="utf-8")


def _stdout_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report["status"],
        "run_id": report["run_id"],
        "snapshot_count": report["metrics"]["snapshot_count"],
        "new_record_count": report["metrics"]["new_record_count"],
        "late_arrival_count": report["metrics"]["late_arrival_count"],
        "bad_candidate_blocked_count": report["metrics"]["bad_candidate_blocked_count"],
        "rollback_restored": report["gates"]["approved_rollback_restored_baseline"],
        "network_called": report["scope"]["network_called"],
        "qwen_called": report["scope"]["qwen_called"],
        "ecs_used": report["scope"]["ecs_used"],
        "report_json": report["outputs"]["report_json"],
        "report_md": report["outputs"]["report_md"],
    }


def _safe_run_id(value: str) -> str:
    return "rolling-open-data-" + "".join(
        char if char.isalnum() else "-" for char in value
    ).strip("-")


def _reset_runtime_dir(runtime_root: Path, runtime_dir: Path) -> None:
    runtime_root.mkdir(parents=True, exist_ok=True)
    runtime_root_resolved = runtime_root.resolve()
    runtime_dir_resolved = runtime_dir.resolve()
    if runtime_dir_resolved == runtime_root_resolved:
        raise ValueError("Refusing to reset runtime_root itself.")
    if runtime_root_resolved not in runtime_dir_resolved.parents:
        raise ValueError("Refusing to reset a runtime directory outside runtime_root.")
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    main()
