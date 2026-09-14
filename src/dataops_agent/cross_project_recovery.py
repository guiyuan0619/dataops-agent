from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dataops_agent.audit import IntegrationAuditStore
from dataops_agent.integration import hospital_consumer_regression, hospital_rollback_drill
from dataops_agent.rules import load_csv, quality_report
from dataops_agent.versioning import VersionStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "dataops_cross_project_recovery.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "dataops_cross_project_recovery.md"
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT / "reports" / "cross_project_recovery_runtime"
DEFAULT_BASELINE_CSV = PROJECT_ROOT / "data" / "raw" / "clean_dev.csv"


def run_cross_project_recovery_drill(
    *,
    report_json: Path | str = DEFAULT_REPORT_JSON,
    report_md: Path | str = DEFAULT_REPORT_MD,
    runtime_root: Path | str = DEFAULT_RUNTIME_ROOT,
    baseline_csv: Path | str = DEFAULT_BASELINE_CSV,
    run_id: str | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    run_id = run_id or _safe_run_id(generated_at)
    runtime_dir = Path(runtime_root) / run_id
    runtime_dir.mkdir(parents=True, exist_ok=True)

    store = VersionStore(runtime_dir / "versions.sqlite3", runtime_dir / "versions")
    audit_store = IntegrationAuditStore(runtime_dir / "integration_audit.sqlite3")
    baseline = load_csv(str(baseline_csv))
    baseline_report = quality_report(baseline, dataset_name="doctors")

    stable_version = store.publish(
        dataset_name="doctors",
        df=baseline,
        quality_report=baseline_report,
        approved=True,
        idempotency_key=f"{run_id}-stable",
    )

    safe_candidate = baseline.copy()
    safe_candidate.at[0, "title"] = "Senior Chief Physician"
    safe_candidate_path = runtime_dir / "safe_candidate.csv"
    safe_candidate.to_csv(safe_candidate_path, index=False)
    safe_regression = hospital_consumer_regression(
        dataset_name="doctors",
        dataset_path=safe_candidate_path,
        store=store,
        consumer_agent="hospital_agent",
        audit_store=audit_store,
        trace_id=f"{run_id}-safe-regression",
    )
    new_version = store.publish(
        dataset_name="doctors",
        df=safe_candidate,
        quality_report=quality_report(safe_candidate, dataset_name="doctors"),
        approved=True,
        parent_version_id=stable_version.version_id,
        expected_active_version_id=stable_version.version_id,
        require_expected_active_match=True,
        idempotency_key=f"{run_id}-safe-publish",
    )

    bad_candidate = safe_candidate[safe_candidate["doctor_id"] != "D005"].copy()
    bad_candidate_path = runtime_dir / "bad_candidate_missing_consumer_key.csv"
    bad_candidate.to_csv(bad_candidate_path, index=False)
    bad_regression = hospital_consumer_regression(
        dataset_name="doctors",
        dataset_path=bad_candidate_path,
        store=store,
        consumer_agent="hospital_agent",
        audit_store=audit_store,
        trace_id=f"{run_id}-bad-regression",
    )
    publish_error = ""
    try:
        store.publish(
            dataset_name="doctors",
            df=bad_candidate,
            quality_report=quality_report(bad_candidate, dataset_name="doctors"),
            approved=True,
            parent_version_id=new_version.version_id,
            expected_active_version_id=new_version.version_id,
            require_expected_active_match=True,
            idempotency_key=f"{run_id}-bad-publish",
        )
    except ValueError as exc:
        publish_error = str(exc)
        audit_store.record_event(
            trace_id=f"{run_id}-bad-publish",
            operation="publish_blocked",
            consumer_agent="hospital_agent",
            dataset_name="doctors",
            status="blocked_before_active_switch",
            payload={"error": publish_error},
        )
    active_after_bad_publish = store.active_version("doctors")

    rollback_request = hospital_rollback_drill(
        dataset_name="doctors",
        target_version_id=stable_version.version_id,
        store=store,
        approved=False,
        consumer_agent="hospital_agent",
        audit_store=audit_store,
        trace_id=f"{run_id}-rollback-request",
    )
    active_after_unapproved_rollback = store.active_version("doctors")

    approved_rollback = hospital_rollback_drill(
        dataset_name="doctors",
        target_version_id=stable_version.version_id,
        store=store,
        approved=True,
        consumer_agent="dataops_agent",
        audit_store=audit_store,
        trace_id=f"{run_id}-approved-rollback",
    )
    restored_active = store.active_version("doctors")
    restored_regression = hospital_consumer_regression(
        dataset_name="doctors",
        dataset_path=Path(stable_version.path),
        store=store,
        consumer_agent="hospital_agent",
        audit_store=audit_store,
        trace_id=f"{run_id}-restored-regression",
    )

    audit_events = audit_store.list_events(limit=200)
    scenarios = _scenario_results(
        safe_regression=safe_regression,
        new_version_id=new_version.version_id,
        bad_regression=bad_regression,
        publish_error=publish_error,
        active_after_bad_publish_id=(
            active_after_bad_publish.version_id if active_after_bad_publish else None
        ),
        active_after_unapproved_rollback_id=(
            active_after_unapproved_rollback.version_id
            if active_after_unapproved_rollback
            else None
        ),
        rollback_request=rollback_request,
        approved_rollback=approved_rollback,
        restored_active_id=restored_active.version_id if restored_active else None,
        stable_version_id=stable_version.version_id,
        restored_regression=restored_regression,
        audit_events=audit_events,
    )
    failed_scenarios = [scenario for scenario in scenarios if not scenario["passed"]]
    report = {
        "status": "ok" if not failed_scenarios else "failed",
        "run_id": run_id,
        "generated_at": generated_at,
        "runtime_dir": str(runtime_dir),
        "outputs": {
            "report_json": str(Path(report_json)),
            "report_md": str(Path(report_md)),
        },
        "scope": {
            "local_only": True,
            "ecs_used_this_run": False,
            "qwen_called_this_run": False,
            "production_traffic": False,
            "hospital_production_system_connected": False,
            "dataops_writes_hospital_index": False,
        },
        "versions": {
            "stable_version_id": stable_version.version_id,
            "safe_published_version_id": new_version.version_id,
            "active_after_bad_publish": (
                active_after_bad_publish.version_id if active_after_bad_publish else None
            ),
            "active_after_unapproved_rollback": (
                active_after_unapproved_rollback.version_id
                if active_after_unapproved_rollback
                else None
            ),
            "restored_active_version_id": restored_active.version_id if restored_active else None,
        },
        "consumer_results": {
            "safe_regression": _compact_consumer_result(safe_regression),
            "bad_regression": _compact_consumer_result(bad_regression),
            "rollback_request": _compact_rollback_result(rollback_request),
            "approved_rollback": _compact_rollback_result(approved_rollback),
            "restored_regression": _compact_consumer_result(restored_regression),
        },
        "metrics": {
            "scenario_count": len(scenarios),
            "passed_scenario_count": len(scenarios) - len(failed_scenarios),
            "bad_candidate_blocked": bool(publish_error),
            "active_preserved_after_bad_publish": (
                active_after_bad_publish is not None
                and active_after_bad_publish.version_id == new_version.version_id
            ),
            "rollback_requires_dataops_approval": rollback_request["status"]
            == "rollback_requires_approval",
            "approved_rollback_executed": approved_rollback["rollback_executed"],
            "restored_active_matches_stable": (
                restored_active is not None
                and restored_active.version_id == stable_version.version_id
            ),
            "restored_regression_passed": restored_regression["release_gate_passed"],
            "audit_event_count": len(audit_events),
            "failed_scenario_count": len(failed_scenarios),
        },
        "scenarios": scenarios,
        "audit_summary": {
            "event_count": len(audit_events),
            "operations": sorted({event["operation"] for event in audit_events}),
            "statuses": sorted({event["status"] for event in audit_events}),
        },
        "resume_ready_metrics": [
            (
                "DataOps 已补跨项目恢复演练：坏知识候选先被下游回归阻断，"
                "active 版本不切换；经 DataOps 审批后可回滚到上一稳定版本。"
            ),
            (
                "这组结果适合放项目展示和面试追问，不建议塞进一页主简历，"
                "避免小规模演练挤掉更强的主指标。"
            ),
        ],
        "interview_ready_answer": (
            "我补过一个跨项目恢复演练：先发布一个可用版本，再故意准备一个"
            "会破坏医院消费者关键记录的坏候选。系统在发布前回归阶段就把它挡住，"
            "正式入口没有切到坏版本；医院侧只能提出恢复诉求，不能自己切换版本。"
            "之后 DataOps 走审批边界回滚到上一稳定版本，并重新跑消费者回归确认可用。"
            "所以这里验证的是发布失败怎么拦、出了问题怎么恢复，而不是声称接入了"
            "真实医院生产流量。"
        ),
        "boundary": {
            "can_say": [
                "DataOps 有本地跨项目恢复演练，覆盖坏候选阻断、回滚审批和恢复后回归。",
                "医院 Agent 是下游消费者，可以暴露回归失败和恢复诉求，但不能直接切版本。",
                "发布失败时 active 版本保持在上一可用版本，回滚后再跑消费者回归确认恢复。",
            ],
            "cannot_say": [
                "不能说这是医院真实生产事故恢复演练。",
                "不能说 DataOps 自动写医院 Milvus 或生产索引。",
                "不能说已经有生产级灰度、多消费者订阅、A2A 或消息队列恢复平台。",
            ],
        },
    }
    _write_json(report_json, report)
    _write_markdown(report_md, report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run DataOps to Hospital Agent cross-project recovery drill."
    )
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument("--baseline-csv", default=str(DEFAULT_BASELINE_CSV))
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    report = run_cross_project_recovery_drill(
        report_json=args.report_json,
        report_md=args.report_md,
        runtime_root=args.runtime_root,
        baseline_csv=args.baseline_csv,
        run_id=args.run_id,
    )
    print(json.dumps(_stdout_summary(report), ensure_ascii=False, indent=2))
    if report["status"] != "ok":
        raise SystemExit(1)


def _scenario_results(
    *,
    safe_regression: dict[str, Any],
    new_version_id: str,
    bad_regression: dict[str, Any],
    publish_error: str,
    active_after_bad_publish_id: str | None,
    active_after_unapproved_rollback_id: str | None,
    rollback_request: dict[str, Any],
    approved_rollback: dict[str, Any],
    restored_active_id: str | None,
    stable_version_id: str,
    restored_regression: dict[str, Any],
    audit_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        _scenario(
            "safe_candidate_can_pass_consumer_regression",
            "干净候选发布前能通过医院消费者回归。",
            safe_regression["release_gate_passed"] is True,
            {"consumer_status": safe_regression["status"]},
        ),
        _scenario(
            "bad_candidate_blocked_before_active_switch",
            "坏候选缺少消费者依赖记录时，发布前被阻断，active 不切到坏版本。",
            bool(publish_error) and active_after_bad_publish_id == new_version_id,
            {
                "consumer_status": bad_regression["status"],
                "active_after_bad_publish": active_after_bad_publish_id,
            },
        ),
        _scenario(
            "consumer_failure_is_traceable",
            "消费者失败能通过审计事件定位到本次回归和阻断。",
            any(event["status"] == "failed_regression" for event in audit_events)
            and any(event["operation"] == "publish_blocked" for event in audit_events),
            {"audit_event_count": len(audit_events)},
        ),
        _scenario(
            "hospital_consumer_cannot_switch_rollback_by_itself",
            "医院消费者没有审批时只能提出恢复诉求，不能自己切换版本。",
            rollback_request["status"] == "rollback_requires_approval"
            and rollback_request["rollback_executed"] is False
            and active_after_unapproved_rollback_id == new_version_id,
            {"rollback_status": rollback_request["status"]},
        ),
        _scenario(
            "dataops_approved_rollback_restores_previous_version",
            "DataOps 审批后能把 active 切回上一稳定版本。",
            approved_rollback["rollback_executed"] is True
            and restored_active_id == stable_version_id,
            {"restored_active": restored_active_id},
        ),
        _scenario(
            "restored_version_passes_consumer_regression",
            "恢复后重新跑医院消费者回归，确认已回到可用版本。",
            restored_regression["release_gate_passed"] is True,
            {"consumer_status": restored_regression["status"]},
        ),
    ]


def _scenario(
    scenario_id: str,
    title: str,
    passed: bool,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": scenario_id,
        "title": title,
        "passed": passed,
        "evidence": evidence,
    }


def _compact_consumer_result(result: dict[str, Any]) -> dict[str, Any]:
    metrics = result["metrics"]
    return {
        "status": result["status"],
        "release_gate_passed": result["release_gate_passed"],
        "candidate_case_count": metrics["candidate_case_count"],
        "baseline_case_count": metrics["baseline_case_count"],
        "baseline_key_coverage": metrics["baseline_key_coverage"],
        "missing_baseline_key_count": metrics["missing_baseline_key_count"],
        "changed_case_count": metrics["changed_case_count"],
    }


def _compact_rollback_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result["status"],
        "rollback_executed": result["rollback_executed"],
        "target_version_id": result["target_version_id"],
        "active_version_before": _version_id(result["active_version_before"]),
        "active_version_after": _version_id(result["active_version_after"]),
    }


def _version_id(record: dict[str, Any] | None) -> str | None:
    if record is None:
        return None
    return str(record["version_id"])


def _write_json(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_markdown(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    metrics = report["metrics"]
    lines = [
        "# DataOps 到医院 Agent 的跨项目恢复演练",
        "",
        f"- 运行 ID：`{report['run_id']}`",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 结果：`{report['status']}`",
        "- 范围：本地跨项目恢复演练；本轮未启用 ECS，未调用 Qwen，非医院生产流量。",
        "",
        "## 大白话结论",
        "",
        report["interview_ready_answer"],
        "",
        "## 核心指标",
        "",
        "| 维度 | 指标 |",
        "|---|---|",
        (
            "| 演练场景 | "
            f"{metrics['passed_scenario_count']}/{metrics['scenario_count']} 通过 |"
        ),
        f"| 坏候选阻断 | {metrics['bad_candidate_blocked']} |",
        f"| 坏候选后 active 保持 | {metrics['active_preserved_after_bad_publish']} |",
        f"| 回滚审批边界 | {metrics['rollback_requires_dataops_approval']} |",
        f"| DataOps 批准回滚 | {metrics['approved_rollback_executed']} |",
        f"| 恢复后回归 | {metrics['restored_regression_passed']} |",
        f"| 审计事件 | {metrics['audit_event_count']} 条 |",
        "",
        "## 场景检查",
        "",
        "| 场景 | 结果 | 说明 |",
        "|---|---|---|",
    ]
    for scenario in report["scenarios"]:
        lines.append(f"| {scenario['id']} | {scenario['passed']} | {scenario['title']} |")
    lines.extend(
        [
            "",
            "## 简历判断",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in report["resume_ready_metrics"])
    lines.extend(["", "## 可以说", ""])
    lines.extend(f"- {item}" for item in report["boundary"]["can_say"])
    lines.extend(["", "## 不要这样说", ""])
    lines.extend(f"- {item}" for item in report["boundary"]["cannot_say"])
    lines.append("")
    destination.write_text("\n".join(lines), encoding="utf-8")


def _safe_run_id(value: str) -> str:
    return "cross-project-recovery-" + "".join(
        char if char.isalnum() else "-" for char in value
    ).strip("-")


def _stdout_summary(report: dict[str, Any]) -> dict[str, Any]:
    metrics = report["metrics"]
    return {
        "status": report["status"],
        "scenario_count": metrics["scenario_count"],
        "passed_scenario_count": metrics["passed_scenario_count"],
        "bad_candidate_blocked": metrics["bad_candidate_blocked"],
        "active_preserved_after_bad_publish": metrics["active_preserved_after_bad_publish"],
        "approved_rollback_executed": metrics["approved_rollback_executed"],
        "restored_regression_passed": metrics["restored_regression_passed"],
        "report_json": report["outputs"]["report_json"],
        "report_md": report["outputs"]["report_md"],
    }


if __name__ == "__main__":
    main()
