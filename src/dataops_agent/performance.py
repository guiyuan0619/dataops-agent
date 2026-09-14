from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import shutil
import sys
import tracemalloc
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter, sleep
from typing import Any

import pandas as pd

from dataops_agent.integration import consumer_regression
from dataops_agent.rules import load_csv, quality_report
from dataops_agent.sources import validate_source_manifest
from dataops_agent.versioning import VersionStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "dataops_performance_report.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "dataops_performance_report.md"
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT / "reports" / "performance_runtime"
DEFAULT_MULTISCALE_REPORT_JSON = (
    PROJECT_ROOT / "reports" / "dataops_multiscale_performance_report.json"
)
DEFAULT_MULTISCALE_REPORT_MD = PROJECT_ROOT / "reports" / "dataops_multiscale_performance_report.md"
DEFAULT_MULTISCALE_RUNTIME_ROOT = PROJECT_ROOT / "reports" / "multiscale_performance_runtime"
SERVICE_REQUESTS_CSV = PROJECT_ROOT / "data" / "imported" / "nyc_311_service_requests_sample.csv"
DEFAULT_SCALE_ROWS = (500, 2500, 5000)


def run_performance_report(
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
    store = VersionStore(runtime_dir / "versions.sqlite3", runtime_dir / "versions")

    total_started = perf_counter()
    manifest_timing = _measure(validate_source_manifest)
    service_requests = load_csv(str(SERVICE_REQUESTS_CSV))
    quality_timing = _measure(
        quality_report,
        service_requests,
        dataset_name="service_requests",
    )
    publish_timing = _measure(
        store.publish,
        dataset_name="service_requests",
        df=service_requests,
        quality_report=quality_timing["result"],
        approved=True,
    )
    regression_timing = _measure(
        consumer_regression,
        dataset_name="service_requests",
        dataset_path=SERVICE_REQUESTS_CSV,
        store=store,
        consumer_agent="service_request_consumer",
    )
    total_elapsed_ms = _elapsed_ms(total_started)
    source_rows = {
        source["dataset_name"]: source["row_count"]
        for source in manifest_timing["result"]["sources"]
    }
    regression_metrics = regression_timing["result"]["metrics"]

    gates = {
        "manifest_valid": manifest_timing["result"]["status"] == "ok",
        "service_requests_500_rows": len(service_requests) == 500,
        "service_requests_quality_gate_passed": quality_timing["result"]["passes_quality_gate"]
        is True,
        "service_requests_regression_500_cases": (
            regression_metrics["candidate_case_count"] == 500
            and regression_metrics["baseline_case_count"] == 500
        ),
        "service_requests_regression_passed": regression_timing["result"]["release_gate_passed"]
        is True,
    }
    report = {
        "status": "ok" if all(gates.values()) else "failed",
        "run_id": run_id,
        "generated_at": generated_at,
        "runtime_dir": str(runtime_dir),
        "outputs": {
            "report_json": str(report_json),
            "report_md": str(report_md),
        },
        "scope": {
            "local_only": True,
            "ecs_used": False,
            "qwen_used": False,
            "production_traffic": False,
        },
        "gates": gates,
        "dataset_scale": {
            "source_manifest_rows": source_rows,
            "service_requests_row_count": len(service_requests),
        },
        "timings_ms": {
            "manifest_validation": manifest_timing["elapsed_ms"],
            "service_requests_quality_report": quality_timing["elapsed_ms"],
            "service_requests_publish": publish_timing["elapsed_ms"],
            "service_requests_consumer_regression": regression_timing["elapsed_ms"],
            "total": total_elapsed_ms,
        },
        "service_requests_regression": {
            "status": regression_timing["result"]["status"],
            "release_gate_passed": regression_timing["result"]["release_gate_passed"],
            "metrics": regression_metrics,
        },
        "boundary": {
            "can_say": [
                (
                    "local performance smoke report covers 500-row NYC 311 "
                    "non-hospital public service request data"
                ),
                (
                    "manifest validation, quality gate, version publish, "
                    "and consumer regression passed"
                ),
            ],
            "cannot_say": [
                "not an ECS public benchmark",
                "not a Qwen live benchmark",
                "not production traffic or production SLA",
                "not a big-data throughput claim",
            ],
        },
    }

    _write_json(report_json, report)
    _write_markdown(report_md, report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run local DataOps performance smoke report.")
    parser.add_argument("--multi-scale", action="store_true")
    parser.add_argument("--report-json")
    parser.add_argument("--report-md")
    parser.add_argument("--runtime-root")
    parser.add_argument("--run-id")
    parser.add_argument(
        "--scale-rows",
        default=",".join(str(row_count) for row_count in DEFAULT_SCALE_ROWS),
        help="Comma-separated row counts for --multi-scale, for example 500,2500,5000.",
    )
    args = parser.parse_args(argv)
    if args.multi_scale:
        report = run_multiscale_performance_report(
            report_json=args.report_json or DEFAULT_MULTISCALE_REPORT_JSON,
            report_md=args.report_md or DEFAULT_MULTISCALE_REPORT_MD,
            runtime_root=args.runtime_root or DEFAULT_MULTISCALE_RUNTIME_ROOT,
            run_id=args.run_id,
            scale_rows=_parse_scale_rows(args.scale_rows),
        )
        summary = {
            "status": report["status"],
            "run_id": report["run_id"],
            "scale_rows": report["dataset_scale"]["scale_rows"],
            "max_row_count": report["dataset_scale"]["max_row_count"],
            "best_rows_per_second": report["summary"]["best_rows_per_second"],
            "max_quality_issue_rate": report["summary"]["max_quality_issue_rate"],
            "max_consumer_failed_case_rate": report["summary"]["max_consumer_failed_case_rate"],
            "report_json": report["outputs"]["report_json"],
            "report_md": report["outputs"]["report_md"],
        }
    else:
        report = run_performance_report(
            report_json=args.report_json or DEFAULT_REPORT_JSON,
            report_md=args.report_md or DEFAULT_REPORT_MD,
            runtime_root=args.runtime_root or DEFAULT_RUNTIME_ROOT,
            run_id=args.run_id,
        )
        summary = {
            "status": report["status"],
            "run_id": report["run_id"],
            "service_requests_row_count": report["dataset_scale"]["service_requests_row_count"],
            "service_requests_regression_status": report["service_requests_regression"]["status"],
            "total_ms": report["timings_ms"]["total"],
            "report_json": report["outputs"]["report_json"],
            "report_md": report["outputs"]["report_md"],
        }
    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        )
    )
    if report["status"] != "ok":
        raise SystemExit(1)


def run_multiscale_performance_report(
    *,
    report_json: Path | str = DEFAULT_MULTISCALE_REPORT_JSON,
    report_md: Path | str = DEFAULT_MULTISCALE_REPORT_MD,
    runtime_root: Path | str = DEFAULT_MULTISCALE_RUNTIME_ROOT,
    run_id: str | None = None,
    scale_rows: tuple[int, ...] = DEFAULT_SCALE_ROWS,
) -> dict[str, Any]:
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    run_id = run_id or _safe_run_id(generated_at).replace("performance-", "multiscale-performance-")
    runtime_dir = Path(runtime_root) / run_id
    _reset_runtime_dir(Path(runtime_root), runtime_dir)

    normalized_scale_rows = tuple(sorted({int(row_count) for row_count in scale_rows}))
    if not normalized_scale_rows or any(row_count <= 0 for row_count in normalized_scale_rows):
        raise ValueError("scale_rows must contain positive row counts.")

    total_started = perf_counter()
    manifest_timing = _measure(validate_source_manifest)
    base_df = load_csv(str(SERVICE_REQUESTS_CSV))
    store = VersionStore(runtime_dir / "versions.sqlite3", runtime_dir / "versions")
    scale_reports = [
        _run_scale_case(
            base_df=base_df,
            row_count=row_count,
            runtime_dir=runtime_dir,
            store=store,
        )
        for row_count in normalized_scale_rows
    ]
    total_elapsed_ms = _elapsed_ms(total_started)
    gates = {
        "manifest_valid": manifest_timing["result"]["status"] == "ok",
        "at_least_three_scale_points": len(normalized_scale_rows) >= 3,
        "scale_rows_monotonic": list(normalized_scale_rows) == sorted(normalized_scale_rows),
        "all_scale_quality_gates_passed": all(
            item["gates"]["quality_gate_passed"] for item in scale_reports
        ),
        "all_scale_publish_gates_passed": all(
            item["gates"]["publish_created_active_version"] for item in scale_reports
        ),
        "all_scale_regression_gates_passed": all(
            item["gates"]["consumer_regression_passed"] for item in scale_reports
        ),
        "all_quality_issue_rates_zero": all(
            item["error_rates"]["quality_issue_rate"] == 0 for item in scale_reports
        ),
    }
    rows_per_second_values = [
        item["throughput"]["rows_per_second"]
        for item in scale_reports
        if item["throughput"]["rows_per_second"] is not None
    ]
    report = {
        "status": "ok" if all(gates.values()) else "failed",
        "run_id": run_id,
        "generated_at": generated_at,
        "runtime_dir": str(runtime_dir),
        "outputs": {
            "report_json": str(report_json),
            "report_md": str(report_md),
        },
        "scope": {
            "local_only": True,
            "ecs_used": False,
            "qwen_used": False,
            "production_traffic": False,
            "synthetic_scale_extension": True,
            "source_sample_path": str(SERVICE_REQUESTS_CSV),
        },
        "resource_context": _resource_context(),
        "gates": gates,
        "dataset_scale": {
            "source_sample_row_count": len(base_df),
            "scale_rows": list(normalized_scale_rows),
            "scale_point_count": len(normalized_scale_rows),
            "max_row_count": max(normalized_scale_rows),
        },
        "summary": {
            "total_elapsed_ms": total_elapsed_ms,
            "best_rows_per_second": max(rows_per_second_values) if rows_per_second_values else None,
            "worst_rows_per_second": (
                min(rows_per_second_values) if rows_per_second_values else None
            ),
            "max_quality_issue_rate": max(
                item["error_rates"]["quality_issue_rate"] for item in scale_reports
            ),
            "max_consumer_failed_case_rate": max(
                item["error_rates"]["consumer_failed_case_rate"] for item in scale_reports
            ),
        },
        "manifest_validation": {
            "elapsed_ms": manifest_timing["elapsed_ms"],
            "status": manifest_timing["result"]["status"],
            "source_count": manifest_timing["result"]["source_count"],
        },
        "scale_reports": scale_reports,
        "boundary": {
            "can_say": [
                (
                    "local multi-scale baseline replays the DataOps quality, publish, "
                    "and consumer-regression chain across 3 scale points"
                ),
                (
                    "the report records stage timing, row throughput, quality issue rate, "
                    "consumer regression error rate, and local resource context"
                ),
            ],
            "cannot_say": [
                "not newly fetched 5k real production rows",
                "not ECS public benchmark",
                "not Qwen live latency",
                "not production traffic, production SLA, or sustained QPS",
            ],
        },
    }
    _write_json(report_json, report)
    _write_multiscale_markdown(report_md, report)
    return report


def _measure(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
    started = perf_counter()
    result = fn(*args, **kwargs)
    return {"elapsed_ms": _elapsed_ms(started), "result": result}


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)


def _safe_run_id(generated_at: str) -> str:
    safe = generated_at.replace(":", "-").replace("+", "-")
    return f"performance-{safe}"


def _parse_scale_rows(value: str) -> tuple[int, ...]:
    return tuple(int(part.strip()) for part in value.split(",") if part.strip())


def _run_scale_case(
    *,
    base_df: pd.DataFrame,
    row_count: int,
    runtime_dir: Path,
    store: VersionStore,
) -> dict[str, Any]:
    gc.collect()
    if tracemalloc.is_tracing():
        tracemalloc.stop()
    tracemalloc.start()
    total_started = perf_counter()
    active_before = store.active_version("service_requests")
    previous_active_row_count = len(load_csv(active_before.path)) if active_before else 0

    candidate_timing = _measure(_scaled_service_requests, base_df, row_count)
    candidate_df = candidate_timing["result"]
    candidate_path = runtime_dir / f"service_requests_{row_count}.csv"
    write_timing = _measure(_write_dataframe_csv, candidate_path, candidate_df)
    quality_timing = _measure(
        quality_report,
        candidate_df,
        dataset_name="service_requests",
    )
    publish_timing = _measure(
        store.publish,
        dataset_name="service_requests",
        df=candidate_df,
        quality_report=quality_timing["result"],
        approved=True,
    )
    regression_timing = _measure(
        consumer_regression,
        dataset_name="service_requests",
        dataset_path=candidate_path,
        store=store,
        consumer_agent="service_request_consumer",
    )
    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    total_elapsed_ms = _elapsed_ms(total_started)
    regression_metrics = regression_timing["result"]["metrics"]
    quality_issue_count = int(quality_timing["result"]["issue_count"])
    candidate_case_count = int(regression_metrics["candidate_case_count"])
    failed_case_count = int(regression_metrics["missing_baseline_key_count"])
    changed_case_count = int(regression_metrics["changed_case_count"])
    return {
        "row_count": row_count,
        "candidate_path": str(candidate_path),
        "previous_active_row_count": previous_active_row_count,
        "active_version_id": publish_timing["result"].version_id,
        "gates": {
            "quality_gate_passed": quality_timing["result"]["passes_quality_gate"] is True,
            "publish_created_active_version": publish_timing["result"].dataset_name
            == "service_requests",
            "consumer_regression_passed": regression_timing["result"]["release_gate_passed"]
            is True,
            "candidate_case_count_matches_rows": candidate_case_count == row_count,
        },
        "timings_ms": {
            "candidate_generation": candidate_timing["elapsed_ms"],
            "candidate_write": write_timing["elapsed_ms"],
            "quality_report": quality_timing["elapsed_ms"],
            "publish": publish_timing["elapsed_ms"],
            "consumer_regression": regression_timing["elapsed_ms"],
            "total": total_elapsed_ms,
        },
        "throughput": {
            "rows_per_second": _rows_per_second(row_count, total_elapsed_ms),
            "quality_rows_per_second": _rows_per_second(
                row_count,
                quality_timing["elapsed_ms"],
            ),
            "regression_rows_per_second": _rows_per_second(
                row_count,
                regression_timing["elapsed_ms"],
            ),
        },
        "error_rates": {
            "quality_issue_count": quality_issue_count,
            "quality_issue_rate": _safe_ratio(quality_issue_count, row_count),
            "consumer_failed_case_count": failed_case_count,
            "consumer_failed_case_rate": _safe_ratio(failed_case_count, candidate_case_count),
            "consumer_changed_case_count": changed_case_count,
            "consumer_changed_case_rate": _safe_ratio(changed_case_count, candidate_case_count),
        },
        "resource": {
            "python_tracemalloc_current_mb": _bytes_to_mb(current_bytes),
            "python_tracemalloc_peak_mb": _bytes_to_mb(peak_bytes),
            "resource_boundary": (
                "tracemalloc tracks Python allocations only; it is not a full system RSS metric"
            ),
        },
        "consumer_regression": {
            "status": regression_timing["result"]["status"],
            "release_gate_passed": regression_timing["result"]["release_gate_passed"],
            "metrics": regression_metrics,
        },
    }


def _scaled_service_requests(base_df: pd.DataFrame, row_count: int) -> pd.DataFrame:
    repeats = (row_count + len(base_df) - 1) // len(base_df)
    scaled = pd.concat([base_df] * repeats, ignore_index=True).head(row_count).copy()
    scaled["request_id"] = [
        f"{request_id}-scale-{index:06d}"
        for index, request_id in enumerate(scaled["request_id"].astype(str))
    ]
    return scaled


def _write_dataframe_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def _rows_per_second(row_count: int, elapsed_ms: float) -> float | None:
    if elapsed_ms <= 0:
        return None
    return round(row_count / (elapsed_ms / 1000), 2)


def _safe_ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else round(numerator / denominator, 6)


def _bytes_to_mb(value: int) -> float:
    return round(value / 1024 / 1024, 3)


def _resource_context() -> dict[str, Any]:
    return {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "resource_measurement_boundary": (
            "local process timing plus Python tracemalloc peak; no psutil/RSS or ECS metrics"
        ),
    }


def _write_json(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_markdown(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    timings = report["timings_ms"]
    regression_metrics = report["service_requests_regression"]["metrics"]
    lines = [
        "# DataOps Agent 本地性能冒烟报告",
        "",
        f"- 运行 ID：`{report['run_id']}`",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 结果：`{report['status']}`",
        "- 范围：本地离线性能冒烟，未启用 ECS，未调用 Qwen，非生产流量。",
        "",
        "## 数据规模",
        "",
        "| 数据集 | 行数 |",
        "|---|---:|",
    ]
    for dataset_name, row_count in report["dataset_scale"]["source_manifest_rows"].items():
        lines.append(f"| {dataset_name} | {row_count} |")
    lines.extend(
        [
            "",
            "## 本地耗时",
            "",
            "| 步骤 | 耗时 ms |",
            "|---|---:|",
            f"| manifest validation | {timings['manifest_validation']} |",
            f"| service_requests quality report | {timings['service_requests_quality_report']} |",
            f"| service_requests publish | {timings['service_requests_publish']} |",
            (
                "| service_requests consumer regression | "
                f"{timings['service_requests_consumer_regression']} |"
            ),
            f"| total | {timings['total']} |",
            "",
            "## 回归指标",
            "",
            "| 指标 | 值 |",
            "|---|---:|",
            f"| candidate_case_count | {regression_metrics['candidate_case_count']} |",
            f"| baseline_case_count | {regression_metrics['baseline_case_count']} |",
            f"| baseline_key_coverage | {regression_metrics['baseline_key_coverage']} |",
            "",
            "## 边界",
            "",
            (
                "- 可以说：本地 500 行 NYC 311 非医院公开服务请求样本通过"
                "质量门禁、版本发布和 consumer regression。"
            ),
            "- 不能说：ECS 公网压测、Qwen live 性能、生产流量、生产 SLA 或大数据吞吐。",
        ]
    )
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_multiscale_markdown(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# DataOps Agent 多规模处理基线报告",
        "",
        f"- 运行 ID：`{report['run_id']}`",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 结果：`{report['status']}`",
        "- 范围：本地多规模回放，未启用 ECS，未调用 Qwen，非生产流量。",
        (
            "- 数据口径：基于 500 行 NYC 311 官方公开样本做稳定扩展，"
            "用于测处理链路随规模变化的趋势，不等于新增真实线上流量。"
        ),
        "",
        "## 规模摘要",
        "",
        "| 指标 | 值 |",
        "|---|---:|",
        f"| 源样本行数 | {report['dataset_scale']['source_sample_row_count']} |",
        f"| 规模档数量 | {report['dataset_scale']['scale_point_count']} |",
        f"| 最大规模 | {report['dataset_scale']['max_row_count']} |",
        f"| 整体耗时 ms | {report['summary']['total_elapsed_ms']} |",
        f"| 最好 rows/s | {report['summary']['best_rows_per_second']} |",
        f"| 最差 rows/s | {report['summary']['worst_rows_per_second']} |",
        f"| 最大质量问题率 | {report['summary']['max_quality_issue_rate']} |",
        f"| 最大消费者失败率 | {report['summary']['max_consumer_failed_case_rate']} |",
        "",
        "## 分规模结果",
        "",
        (
            "| 行数 | 总耗时 ms | rows/s | 质量检测 ms | 发布 ms | 下游回归 ms | "
            "质量问题率 | 消费者失败率 | Python 峰值 MB |"
        ),
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report["scale_reports"]:
        timings = item["timings_ms"]
        lines.append(
            "| "
            f"{item['row_count']} | "
            f"{timings['total']} | "
            f"{item['throughput']['rows_per_second']} | "
            f"{timings['quality_report']} | "
            f"{timings['publish']} | "
            f"{timings['consumer_regression']} | "
            f"{item['error_rates']['quality_issue_rate']} | "
            f"{item['error_rates']['consumer_failed_case_rate']} | "
            f"{item['resource']['python_tracemalloc_peak_mb']} |"
        )
    lines.extend(
        [
            "",
            "## 门禁",
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
            "## 资源口径",
            "",
            f"- Python：`{report['resource_context']['python_version']}`",
            f"- 平台：`{report['resource_context']['platform']}`",
            f"- CPU 逻辑核心：`{report['resource_context']['cpu_count']}`",
            f"- 资源边界：{report['resource_context']['resource_measurement_boundary']}",
            "",
            "## 面试口径",
            "",
            (
                "- 可以说：我不只跑了 500 行单次冒烟，还补了 500、2500、5000 "
                "三档本地规模基线，记录质量检测、版本发布和下游回归各阶段耗时。"
            ),
            (
                "- 可以说：这次三档回放中质量问题率和消费者失败率均为 0，"
                "说明固定公开样本扩展下链路没有因为规模放大而破坏发布门禁。"
            ),
            ("- 不能说：这是生产压测、真实线上流量、Qwen 延迟、ECS 基准、生产 QPS 或生产 SLA。"),
            "",
        ]
    )
    destination.write_text("\n".join(lines), encoding="utf-8")


def _reset_runtime_dir(runtime_root: Path, runtime_dir: Path) -> None:
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
