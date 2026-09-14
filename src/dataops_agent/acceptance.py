from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dataops_agent.governance import resume_governance_run, start_governance_run
from dataops_agent.integration import (
    consumer_regression,
    consumer_rollback_drill,
    start_consumer_governance,
)
from dataops_agent.rules import load_csv, quality_report
from dataops_agent.sources import validate_source_manifest
from dataops_agent.versioning import VersionStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "dataops_acceptance_report.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "dataops_acceptance_report.md"
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT / "reports" / "acceptance_runtime"

COUNTEREXAMPLE_CATALOG = {
    "source_manifest_traceable": {
        "stage": "source_ingestion",
        "risk": "untraceable_public_source",
        "guardrail": (
            "manifest validates source snapshot, metadata, checksum, schema, "
            "and candidate quality gate"
        ),
        "interview_value": "proves public data is traceable before it becomes a candidate dataset",
    },
    "unapproved_publish_blocked": {
        "stage": "version_publish",
        "risk": "unapproved_formal_write",
        "guardrail": "VersionStore rejects publish calls without explicit approval",
        "interview_value": (
            "proves clean data still cannot become an active version without approval"
        ),
    },
    "dirty_candidate_blocks_consumer_regression": {
        "stage": "consumer_regression",
        "risk": "dirty_candidate_release",
        "guardrail": "consumer regression blocks candidates that fail deterministic quality gates",
        "interview_value": "proves bad data is stopped before downstream consumers see it",
    },
    "missing_baseline_key_blocks_release": {
        "stage": "consumer_regression",
        "risk": "lost_consumer_visible_record",
        "guardrail": "baseline key coverage must remain complete before release",
        "interview_value": (
            "proves a clean-looking candidate can still fail if it drops "
            "downstream-visible records"
        ),
    },
    "unresolved_public_data_issues_require_manual_review": {
        "stage": "governance_approval",
        "risk": "unsafe_auto_repair",
        "guardrail": "unresolved public-source issues route to manual review and do not publish",
        "interview_value": (
            "proves the system refuses to fake deterministic repairs for ambiguous public data"
        ),
    },
    "candidate_checksum_mismatch_blocks_publish": {
        "stage": "governance_resume",
        "risk": "candidate_changed_after_approval",
        "guardrail": (
            "approval resume compares the current candidate checksum with the previewed checksum"
        ),
        "interview_value": (
            "proves approval applies to the reviewed candidate, not any later file contents"
        ),
    },
    "unapproved_rollback_does_not_switch_active_version": {
        "stage": "rollback",
        "risk": "unapproved_active_version_switch",
        "guardrail": (
            "rollback drill reports approval requirement without switching the active version"
        ),
        "interview_value": "proves rollback is controlled by the same write boundary as publish",
    },
}


def run_acceptance(
    *,
    report_json: Path | str = DEFAULT_REPORT_JSON,
    report_md: Path | str = DEFAULT_REPORT_MD,
    runtime_root: Path | str = DEFAULT_RUNTIME_ROOT,
    run_id: str | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    run_id = run_id or _safe_run_id(generated_at)
    runtime_dir = Path(runtime_root) / run_id
    store = VersionStore(runtime_dir / "versions.sqlite3", runtime_dir / "versions")
    checkpoint_path = runtime_dir / "checkpoints.sqlite3"
    paths = _acceptance_paths()
    _require_files(paths)

    source_manifest = validate_source_manifest()
    service_requests = load_csv(str(paths["service_requests_clean"]))
    service_version = store.publish(
        dataset_name="service_requests",
        df=service_requests,
        quality_report=quality_report(service_requests, dataset_name="service_requests"),
        approved=True,
    )

    cases = [
        _source_manifest_case(source_manifest),
        _unapproved_publish_case(store, paths["doctors_clean"]),
        _dirty_consumer_regression_case(store, paths["service_requests_dirty"]),
        _missing_baseline_key_case(
            store,
            service_requests,
            runtime_dir / "service_requests_missing_baseline.csv",
        ),
        _manual_review_case(store, checkpoint_path, paths["cms_hospitals_dirty"]),
        _candidate_checksum_mismatch_case(
            store,
            checkpoint_path,
            paths["doctors_dirty"],
            runtime_dir / "doctors_dirty_candidate_mutated.csv",
        ),
        _unapproved_rollback_case(store, service_version.version_id),
    ]
    passed_count = sum(1 for case in cases if case["passed"])
    report = {
        "status": "ok" if passed_count == len(cases) else "failed",
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
        },
        "summary": {
            "case_count": len(cases),
            "passed_case_count": passed_count,
            "failed_case_count": len(cases) - passed_count,
        },
        "counterexample_catalog": _counterexample_catalog(cases),
        "cases": cases,
        "interview_boundary": {
            "can_say": [
                "本地验收覆盖来源追溯、未审批写入、质量门禁、基线覆盖、人工复核、审批后候选变化拦截和回滚审批。",
                "失败样例会被确定性门禁拦截，不依赖 LLM 判断。",
            ],
            "cannot_say": [
                "生产事故演练",
                "真实线上流量回归",
                "ECS 公网验收",
                "Qwen 参与本轮验收判断",
            ],
        },
    }
    _write_report(Path(report_json), report)
    _write_report_md(Path(report_md), report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run local DataOps Agent acceptance gates.")
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    report = run_acceptance(
        report_json=args.report_json,
        report_md=args.report_md,
        runtime_root=args.runtime_root,
        run_id=args.run_id,
    )
    print(json.dumps(_stdout_summary(report), ensure_ascii=False, indent=2))
    if report["status"] != "ok":
        raise SystemExit(1)


def _source_manifest_case(source_manifest: dict[str, Any]) -> dict[str, Any]:
    return _case(
        "source_manifest_traceable",
        source_manifest["status"] == "ok" and source_manifest["issue_count"] == 0,
        "tracked public source assets are traceable and quality-gated",
        f"status={source_manifest['status']} issue_count={source_manifest['issue_count']}",
        {
            "source_count": source_manifest["source_count"],
            "datasets": source_manifest["datasets"],
        },
    )


def _unapproved_publish_case(store: VersionStore, dataset_path: Path) -> dict[str, Any]:
    df = load_csv(str(dataset_path))
    blocked = False
    error = ""
    try:
        store.publish(
            dataset_name="doctors",
            df=df,
            quality_report=quality_report(df, dataset_name="doctors"),
            approved=False,
        )
    except PermissionError as exc:
        blocked = True
        error = str(exc)
    return _case(
        "unapproved_publish_blocked",
        blocked,
        "publish without explicit approval is rejected",
        error or "publish was not blocked",
        {"dataset_name": "doctors"},
    )


def _dirty_consumer_regression_case(store: VersionStore, dataset_path: Path) -> dict[str, Any]:
    result = consumer_regression(
        dataset_name="service_requests",
        dataset_path=dataset_path,
        store=store,
        consumer_agent="service_request_consumer",
    )
    return _case(
        "dirty_candidate_blocks_consumer_regression",
        result["status"] == "blocked_quality_gate"
        and result["release_gate_passed"] is False
        and result["metrics"]["quality_issue_count"] == 5,
        "dirty service request candidate is blocked before release",
        result["status"],
        {"metrics": result["metrics"]},
    )


def _missing_baseline_key_case(
    store: VersionStore,
    baseline_df,
    candidate_path: Path,
) -> dict[str, Any]:
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_df.iloc[:-1].to_csv(candidate_path, index=False)
    result = consumer_regression(
        dataset_name="service_requests",
        dataset_path=candidate_path,
        store=store,
        consumer_agent="service_request_consumer",
    )
    return _case(
        "missing_baseline_key_blocks_release",
        result["status"] == "failed_regression"
        and result["metrics"]["missing_baseline_key_count"] == 1,
        "candidate missing a baseline consumer key is rejected",
        result["status"],
        {"metrics": result["metrics"], "failed_cases": result["failed_cases"][:3]},
    )


def _manual_review_case(
    store: VersionStore,
    checkpoint_path: Path,
    dataset_path: Path,
) -> dict[str, Any]:
    result = start_consumer_governance(
        thread_id="acceptance-cms-manual-review",
        dataset_name="hospitals",
        dataset_path=dataset_path,
        checkpoint_path=checkpoint_path,
        store=store,
        consumer_agent="hospital_registry_consumer",
    )
    return _case(
        "unresolved_public_data_issues_require_manual_review",
        result["status"] == "manual_review_required"
        and result.get("unresolved_issue_count", 0) > 0
        and "version" not in result,
        "public-source issues without safe deterministic repair are not published",
        result["status"],
        {
            "unresolved_issue_count": result.get("unresolved_issue_count", 0),
            "version_present": "version" in result,
        },
    )


def _candidate_checksum_mismatch_case(
    store: VersionStore,
    checkpoint_path: Path,
    dataset_path: Path,
    candidate_path: Path,
) -> dict[str, Any]:
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(dataset_path.read_text(encoding="utf-8"), encoding="utf-8")
    start_governance_run(
        thread_id="acceptance-mutated-candidate",
        dataset_name="doctors",
        dataset_path=candidate_path,
        checkpoint_path=checkpoint_path,
        version_db_path=store.db_path,
        artifact_dir=store.artifact_dir,
    )
    candidate_path.write_text(
        candidate_path.read_text(encoding="utf-8").replace("Alice Chen", "Alice Changed"),
        encoding="utf-8",
    )
    result = resume_governance_run(
        thread_id="acceptance-mutated-candidate",
        approved=True,
        approver="acceptance",
        checkpoint_path=checkpoint_path,
    )
    active = store.active_version("doctors")
    return _case(
        "candidate_checksum_mismatch_blocks_publish",
        result["status"] == "candidate_changed_after_approval"
        and result["expected_candidate_checksum"] != result["actual_candidate_checksum"]
        and active is None,
        "candidate changed after approval preview is not published",
        result["status"],
        {
            "active_version_present": active is not None,
            "expected_candidate_checksum": result.get("expected_candidate_checksum"),
            "actual_candidate_checksum": result.get("actual_candidate_checksum"),
        },
    )


def _unapproved_rollback_case(store: VersionStore, target_version_id: str) -> dict[str, Any]:
    active_before = store.active_version("service_requests")
    result = consumer_rollback_drill(
        dataset_name="service_requests",
        target_version_id=target_version_id,
        store=store,
        approved=False,
        consumer_agent="service_request_consumer",
    )
    active_after = result["active_version_after"]
    return _case(
        "unapproved_rollback_does_not_switch_active_version",
        result["status"] == "rollback_requires_approval"
        and result["rollback_executed"] is False
        and active_after["version_id"] == active_before.version_id,
        "rollback drill requires explicit approval before active version switch",
        result["status"],
        {
            "rollback_executed": result["rollback_executed"],
            "active_before_id": active_before.version_id,
            "active_after_id": active_after["version_id"],
        },
    )


def _case(
    name: str,
    passed: bool,
    expected: str,
    observed: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    catalog = COUNTEREXAMPLE_CATALOG[name]
    return {
        "name": name,
        **catalog,
        "passed": bool(passed),
        "expected": expected,
        "observed": observed,
        "evidence": evidence,
    }


def _counterexample_catalog(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": case["name"],
            "stage": case["stage"],
            "risk": case["risk"],
            "guardrail": case["guardrail"],
            "interview_value": case["interview_value"],
            "passed": case["passed"],
        }
        for case in cases
    ]


def _acceptance_paths() -> dict[str, Path]:
    return {
        "doctors_clean": PROJECT_ROOT / "data" / "raw" / "clean_dev.csv",
        "doctors_dirty": PROJECT_ROOT / "data" / "derived" / "dirty_dev.csv",
        "service_requests_clean": PROJECT_ROOT
        / "data"
        / "imported"
        / "nyc_311_service_requests_sample.csv",
        "service_requests_dirty": PROJECT_ROOT
        / "data"
        / "derived"
        / "nyc_311_service_requests_dirty_sample.csv",
        "cms_hospitals_dirty": PROJECT_ROOT / "data" / "derived" / "cms_hospitals_dirty_sample.csv",
    }


def _require_files(paths: dict[str, Path]) -> None:
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Acceptance input files are missing: {', '.join(missing)}")


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_report_md(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# DataOps Agent 本地验收报告",
        "",
        f"- 运行 ID：`{report['run_id']}`",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 结果：`{report['status']}`",
        "- 范围：本地离线验收，未启用 ECS，未调用 Qwen，非生产流量。",
        "",
        "## 门禁结果",
        "",
        "| 门禁 | 结果 | 期望 | 观测 |",
        "|---|---|---|---|",
    ]
    for case in report["cases"]:
        lines.append(
            f"| {case['name']} | {case['passed']} | {case['expected']} | {case['observed']} |"
        )
    lines.extend(
        [
            "",
            "## 反例库",
            "",
            "| 阶段 | 风险 | 门禁 | 面试价值 |",
            "|---|---|---|---|",
        ]
    )
    for case in report["counterexample_catalog"]:
        lines.append(
            "| "
            f"{case['stage']} | "
            f"{case['risk']} | "
            f"{case['guardrail']} | "
            f"{case['interview_value']} |"
        )
    lines.extend(
        [
            "",
            "## 边界",
            "",
            (
                "- 可以说：本地验收覆盖来源追溯、未审批写入、质量门禁、基线覆盖、"
                "人工复核、审批后候选变化拦截和回滚审批。"
            ),
            "- 不能说：生产事故演练、真实线上流量回归、ECS 公网验收或 Qwen 参与本轮判断。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _stdout_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report["status"],
        "run_id": report["run_id"],
        "case_count": report["summary"]["case_count"],
        "passed_case_count": report["summary"]["passed_case_count"],
        "failed_case_count": report["summary"]["failed_case_count"],
        "report_json": report["outputs"]["report_json"],
        "report_md": report["outputs"]["report_md"],
    }


def _safe_run_id(value: str) -> str:
    return "acceptance-" + "".join(char if char.isalnum() else "-" for char in value).strip("-")


if __name__ == "__main__":
    main()
