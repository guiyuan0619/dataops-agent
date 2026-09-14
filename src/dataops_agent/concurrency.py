from __future__ import annotations

import argparse
import gc
import json
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter, sleep
from typing import Any

import pandas as pd

from dataops_agent.performance import SERVICE_REQUESTS_CSV
from dataops_agent.rules import load_csv, quality_report
from dataops_agent.versioning import (
    ActiveVersionConflictError,
    IdempotencyConflictError,
    VersionStore,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "dataops_concurrency_report.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "dataops_concurrency_report.md"
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT / "reports" / "concurrency_runtime"


def run_concurrency_report(
    *,
    report_json: Path | str = DEFAULT_REPORT_JSON,
    report_md: Path | str = DEFAULT_REPORT_MD,
    runtime_root: Path | str = DEFAULT_RUNTIME_ROOT,
    run_id: str | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    run_id = run_id or _safe_run_id(generated_at)
    runtime_dir = Path(runtime_root) / run_id
    _reset_runtime_dir(Path(runtime_root), runtime_dir)

    started = perf_counter()
    base_df = load_csv(str(SERVICE_REQUESTS_CSV))
    idempotency_case = _run_idempotency_case(runtime_dir, base_df)
    stale_case = _run_stale_active_case(runtime_dir, base_df)
    concurrent_case = _run_concurrent_publish_case(runtime_dir, base_df)
    elapsed_ms = round((perf_counter() - started) * 1000, 3)

    gates = {
        "duplicate_request_reuses_version": idempotency_case["duplicate_reused_version"],
        "idempotency_key_reuse_with_different_data_blocked": idempotency_case[
            "different_candidate_blocked"
        ],
        "stale_expected_active_version_blocked": stale_case["stale_publish_blocked"],
        "concurrent_publish_has_single_winner": concurrent_case["published_count"] == 1,
        "concurrent_publish_has_single_conflict": concurrent_case["conflict_count"] == 1,
        "active_pointer_matches_winner": concurrent_case["active_pointer_matches_winner"],
        "no_failed_case_created_extra_active": concurrent_case["version_count"] == 2,
    }
    report = {
        "status": "ok" if all(gates.values()) else "failed",
        "run_id": run_id,
        "generated_at": generated_at,
        "runtime_dir": str(runtime_dir),
        "outputs": {
            "report_json": str(Path(report_json)),
            "report_md": str(Path(report_md)),
        },
        "scope": {
            "local_only": True,
            "ecs_used": False,
            "qwen_used": False,
            "production_traffic": False,
            "single_process_sqlite": True,
            "distributed_lock_used": False,
            "message_queue_used": False,
        },
        "dataset": {
            "dataset_name": "service_requests",
            "source_path": str(SERVICE_REQUESTS_CSV),
            "row_count": len(base_df),
        },
        "summary": {
            "elapsed_ms": elapsed_ms,
            "idempotent_version_count": idempotency_case["version_count"],
            "concurrent_published_count": concurrent_case["published_count"],
            "concurrent_conflict_count": concurrent_case["conflict_count"],
            "concurrent_version_count": concurrent_case["version_count"],
            "stale_conflict_status": stale_case["status"],
        },
        "gates": gates,
        "cases": {
            "idempotent_duplicate_publish": idempotency_case,
            "stale_expected_active_publish": stale_case,
            "concurrent_publish_same_parent": concurrent_case,
        },
        "boundary": {
            "can_say": [
                (
                    "重复提交同一个已审批发布请求时，系统复用原版本，"
                    "不会静默创建第二个 active version。"
                ),
                (
                    "如果同一个幂等键换了候选数据，或者审批后 active version 已变化，"
                    "系统会显式阻断。"
                ),
                ("两个基于同一旧版本的并发发布请求，只允许一个成功，另一个变成可解释冲突。"),
            ],
            "cannot_say": [
                "不是分布式事务或分布式锁。",
                "没有接入 Kafka、Celery、Airflow 或生产任务队列。",
                "不是多实例生产并发压测，也不是生产 SLA。",
            ],
        },
    }
    _write_json(report_json, report)
    _write_markdown(report_md, report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run DataOps concurrency and idempotency validation."
    )
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    report = run_concurrency_report(
        report_json=args.report_json,
        report_md=args.report_md,
        runtime_root=args.runtime_root,
        run_id=args.run_id,
    )
    print(json.dumps(_stdout_summary(report), ensure_ascii=False, indent=2))
    if report["status"] != "ok":
        raise SystemExit(1)


def _run_idempotency_case(runtime_dir: Path, base_df: pd.DataFrame) -> dict[str, Any]:
    store = VersionStore(runtime_dir / "idempotency.sqlite3", runtime_dir / "idempotent_versions")
    baseline = store.publish(
        dataset_name="service_requests",
        df=base_df,
        quality_report=quality_report(base_df, dataset_name="service_requests"),
        approved=True,
    )
    candidate = _mutate_service_request(base_df, "Noise")
    first = store.publish(
        dataset_name="service_requests",
        df=candidate,
        quality_report=quality_report(candidate, dataset_name="service_requests"),
        approved=True,
        parent_version_id=baseline.version_id,
        expected_active_version_id=baseline.version_id,
        require_expected_active_match=True,
        idempotency_key="same-approved-request",
    )
    repeated = store.publish(
        dataset_name="service_requests",
        df=candidate,
        quality_report=quality_report(candidate, dataset_name="service_requests"),
        approved=True,
        parent_version_id=baseline.version_id,
        expected_active_version_id=baseline.version_id,
        require_expected_active_match=True,
        idempotency_key="same-approved-request",
    )
    changed = _mutate_service_request(base_df, "Blocked Driveway")
    different_candidate_blocked = False
    different_candidate_error = ""
    try:
        store.publish(
            dataset_name="service_requests",
            df=changed,
            quality_report=quality_report(changed, dataset_name="service_requests"),
            approved=True,
            idempotency_key="same-approved-request",
        )
    except IdempotencyConflictError as exc:
        different_candidate_blocked = True
        different_candidate_error = str(exc)
    versions = store.list_versions("service_requests")
    active = store.active_version("service_requests")
    return {
        "status": "passed" if different_candidate_blocked else "failed",
        "baseline_version_id": baseline.version_id,
        "first_version_id": first.version_id,
        "repeated_version_id": repeated.version_id,
        "active_version_id": active.version_id if active else None,
        "version_count": len(versions),
        "duplicate_reused_version": repeated.version_id == first.version_id,
        "different_candidate_blocked": different_candidate_blocked,
        "different_candidate_error": different_candidate_error,
    }


def _run_stale_active_case(runtime_dir: Path, base_df: pd.DataFrame) -> dict[str, Any]:
    store = VersionStore(runtime_dir / "stale-active.sqlite3", runtime_dir / "stale_versions")
    baseline = store.publish(
        dataset_name="service_requests",
        df=base_df,
        quality_report=quality_report(base_df, dataset_name="service_requests"),
        approved=True,
    )
    newer = _mutate_service_request(base_df, "Illegal Parking")
    newer_version = store.publish(
        dataset_name="service_requests",
        df=newer,
        quality_report=quality_report(newer, dataset_name="service_requests"),
        approved=True,
        parent_version_id=baseline.version_id,
        expected_active_version_id=baseline.version_id,
        require_expected_active_match=True,
    )
    stale_candidate = _mutate_service_request(base_df, "Water System")
    stale_publish_blocked = False
    stale_error = ""
    try:
        store.publish(
            dataset_name="service_requests",
            df=stale_candidate,
            quality_report=quality_report(stale_candidate, dataset_name="service_requests"),
            approved=True,
            parent_version_id=baseline.version_id,
            expected_active_version_id=baseline.version_id,
            require_expected_active_match=True,
        )
    except ActiveVersionConflictError as exc:
        stale_publish_blocked = True
        stale_error = str(exc)
    active = store.active_version("service_requests")
    return {
        "status": "stale_active_blocked" if stale_publish_blocked else "failed",
        "baseline_version_id": baseline.version_id,
        "newer_version_id": newer_version.version_id,
        "active_version_id": active.version_id if active else None,
        "version_count": len(store.list_versions("service_requests")),
        "stale_publish_blocked": stale_publish_blocked,
        "stale_error": stale_error,
    }


def _run_concurrent_publish_case(runtime_dir: Path, base_df: pd.DataFrame) -> dict[str, Any]:
    store = VersionStore(runtime_dir / "concurrent.sqlite3", runtime_dir / "concurrent_versions")
    baseline = store.publish(
        dataset_name="service_requests",
        df=base_df,
        quality_report=quality_report(base_df, dataset_name="service_requests"),
        approved=True,
    )

    def publish_candidate(index: int) -> dict[str, Any]:
        candidate = _mutate_service_request(base_df, f"Concurrent Case {index}")
        try:
            record = store.publish(
                dataset_name="service_requests",
                df=candidate,
                quality_report=quality_report(candidate, dataset_name="service_requests"),
                approved=True,
                parent_version_id=baseline.version_id,
                expected_active_version_id=baseline.version_id,
                require_expected_active_match=True,
                idempotency_key=f"concurrent-request-{index}",
            )
        except ActiveVersionConflictError as exc:
            return {"status": "conflict", "version_id": None, "error": str(exc)}
        return {"status": "published", "version_id": record.version_id, "error": ""}

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result()
            for future in as_completed(
                executor.submit(publish_candidate, index) for index in (1, 2)
            )
        ]
    active = store.active_version("service_requests")
    published_version_ids = [
        result["version_id"] for result in results if result["status"] == "published"
    ]
    return {
        "status": "single_winner" if len(published_version_ids) == 1 else "failed",
        "baseline_version_id": baseline.version_id,
        "results": results,
        "published_count": len(published_version_ids),
        "conflict_count": sum(1 for result in results if result["status"] == "conflict"),
        "version_count": len(store.list_versions("service_requests")),
        "active_version_id": active.version_id if active else None,
        "winner_version_id": published_version_ids[0] if published_version_ids else None,
        "active_pointer_matches_winner": (
            active is not None
            and len(published_version_ids) == 1
            and active.version_id == published_version_ids[0]
        ),
    }


def _mutate_service_request(base_df: pd.DataFrame, complaint_type: str) -> pd.DataFrame:
    candidate = base_df.copy()
    candidate.at[0, "complaint_type"] = complaint_type
    return candidate


def _write_json(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_markdown(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# DataOps Agent 并发与幂等验证报告",
        "",
        f"- 运行 ID：`{report['run_id']}`",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 结果：`{report['status']}`",
        "- 范围：本地单进程 SQLite 验证，未启用 ECS，未调用 Qwen，非生产流量。",
        "",
        "## 摘要",
        "",
        "| 指标 | 值 |",
        "|---|---:|",
        f"| 数据行数 | {report['dataset']['row_count']} |",
        f"| 总耗时 ms | {report['summary']['elapsed_ms']} |",
        f"| 幂等场景版本数 | {report['summary']['idempotent_version_count']} |",
        f"| 并发成功数 | {report['summary']['concurrent_published_count']} |",
        f"| 并发冲突数 | {report['summary']['concurrent_conflict_count']} |",
        f"| 并发场景版本数 | {report['summary']['concurrent_version_count']} |",
        "",
        "## 场景结果",
        "",
        "| 场景 | 结果 | 说明 |",
        "|---|---|---|",
        (
            "| 重复提交 | "
            f"`{report['cases']['idempotent_duplicate_publish']['status']}` | "
            "同一幂等键重复提交，复用原版本；同键换数据被拒绝 |"
        ),
        (
            "| 旧 active 发布 | "
            f"`{report['cases']['stale_expected_active_publish']['status']}` | "
            "候选基于旧版本审批，发布前发现 active 已变化后阻断 |"
        ),
        (
            "| 同父版本并发发布 | "
            f"`{report['cases']['concurrent_publish_same_parent']['status']}` | "
            "两个并发请求只有一个切换 active，另一个返回显式冲突 |"
        ),
        "",
        "## 门禁",
        "",
        "| 门禁 | 结果 |",
        "|---|---|",
    ]
    for name, value in report["gates"].items():
        lines.append(f"| `{name}` | `{value}` |")
    lines.extend(
        [
            "",
            "## 面试口径",
            "",
            (
                "- 可以说：我补了重复提交和并发冲突验证，避免同一发布请求"
                "重复创建版本，也避免旧审批覆盖别人刚发布的版本。"
            ),
            (
                "- 可以说：当前做的是单进程 SQLite 下的版本一致性保护，"
                "它能证明工程边界和失败处理，不等同于分布式任务平台。"
            ),
            ("- 不能说：已经实现分布式锁、消息队列、多实例生产压测、生产 SLA 或真实线上并发流量。"),
            "",
        ]
    )
    destination.write_text("\n".join(lines), encoding="utf-8")


def _stdout_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report["status"],
        "run_id": report["run_id"],
        "gates_passed": all(report["gates"].values()),
        "concurrent_published_count": report["summary"]["concurrent_published_count"],
        "concurrent_conflict_count": report["summary"]["concurrent_conflict_count"],
        "report_json": report["outputs"]["report_json"],
        "report_md": report["outputs"]["report_md"],
    }


def _safe_run_id(value: str) -> str:
    return "concurrency-" + "".join(char if char.isalnum() else "-" for char in value).strip("-")


def _reset_runtime_dir(runtime_root: Path, runtime_dir: Path) -> None:
    """Keep report reruns deterministic without deleting outside the runtime root."""
    runtime_root.mkdir(parents=True, exist_ok=True)
    runtime_root_resolved = runtime_root.resolve()
    runtime_dir_resolved = runtime_dir.resolve()
    if runtime_dir_resolved == runtime_root_resolved:
        raise ValueError("Refusing to reset runtime_root itself.")
    if runtime_root_resolved not in runtime_dir_resolved.parents:
        raise ValueError("Refusing to reset a runtime directory outside runtime_root.")
    if runtime_dir.exists():
        _remove_runtime_dir(runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)


def _remove_runtime_dir(runtime_dir: Path) -> None:
    for attempt in range(5):
        try:
            shutil.rmtree(runtime_dir)
            return
        except PermissionError:
            if attempt == 4:
                raise
            gc.collect()
            sleep(0.1)


if __name__ == "__main__":
    main()
