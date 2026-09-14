from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dataops_agent.audit import IntegrationAuditStore
from dataops_agent.downstream_regression import run_hospital_downstream_regression
from dataops_agent.evaluate import evaluate_detection
from dataops_agent.governance import resume_governance_run, thread_status
from dataops_agent.integration import (
    consumer_regression,
    hospital_readiness,
    hospital_rollback_drill,
    start_hospital_governance,
)
from dataops_agent.rules import load_csv, quality_report
from dataops_agent.sources import validate_source_manifest
from dataops_agent.versioning import VersionStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "dataops_local_demo.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "dataops_local_demo.md"
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT / "reports" / "demo_runtime"


def run_local_demo(
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
    audit = IntegrationAuditStore(runtime_dir / "dataops.sqlite3")
    checkpoint_path = runtime_dir / "checkpoints.sqlite3"

    paths = _demo_paths()
    _require_files(paths)
    source_manifest = validate_source_manifest()

    clean_doctors = load_csv(str(paths["clean_doctors"]))
    unapproved_publish_blocked = _unapproved_publish_is_blocked(store, clean_doctors)
    baseline_version = store.publish(
        dataset_name="doctors",
        df=clean_doctors,
        quality_report=quality_report(clean_doctors, dataset_name="doctors"),
        approved=True,
    )

    doctor_metrics = evaluate_detection(
        paths["dirty_doctors"],
        paths["dirty_doctor_labels"],
        dataset_name="doctors",
    )
    readiness = hospital_readiness(
        dataset_name="doctors",
        dataset_path=paths["dirty_doctors"],
        store=store,
        audit_store=audit,
        trace_id=f"{run_id}-doctors-readiness",
    )
    governance_trace_id = f"{run_id}-doctors-governance"
    doctor_governance_thread_id = f"{run_id}-doctor-governance"
    governance_start = start_hospital_governance(
        thread_id=doctor_governance_thread_id,
        dataset_name="doctors",
        dataset_path=paths["dirty_doctors"],
        checkpoint_path=checkpoint_path,
        store=store,
        audit_store=audit,
        trace_id=governance_trace_id,
    )
    saved_state = thread_status(
        thread_id=doctor_governance_thread_id,
        checkpoint_path=checkpoint_path,
    )
    governance_final = resume_governance_run(
        thread_id=doctor_governance_thread_id,
        approved=True,
        approver="local_demo",
        checkpoint_path=checkpoint_path,
    )
    audit.record_event(
        trace_id=governance_trace_id,
        operation="governance_resume",
        consumer_agent="hospital_agent",
        dataset_name="doctors",
        thread_id=doctor_governance_thread_id,
        status=str(governance_final.get("status") or "unknown"),
        payload={
            "approved": True,
            "approver": "local_demo",
            "version_id": governance_final.get("version", {}).get("version_id"),
            "quality_issue_count": governance_final.get("quality_report", {}).get("issue_count"),
        },
    )

    downstream_regression = run_hospital_downstream_regression(
        dataset_path=governance_final["version"]["path"],
        dataset_name="doctors",
        version_id=governance_final["version"]["version_id"],
        knowledge_output=runtime_dir / "hospital_downstream_knowledge.jsonl",
        report_json=runtime_dir / "hospital_downstream_regression.json",
        report_md=runtime_dir / "hospital_downstream_regression.md",
    )
    regression = consumer_regression(
        dataset_name="doctors",
        dataset_path=paths["clean_doctors"],
        store=store,
        consumer_agent="doctor_roster_consumer",
        audit_store=audit,
        trace_id=f"{run_id}-doctors-regression",
    )
    rollback_dry_run = hospital_rollback_drill(
        dataset_name="doctors",
        target_version_id=baseline_version.version_id,
        store=store,
        audit_store=audit,
        trace_id=f"{run_id}-doctors-rollback",
    )
    rollback_approved = hospital_rollback_drill(
        dataset_name="doctors",
        target_version_id=baseline_version.version_id,
        store=store,
        approved=True,
        audit_store=audit,
        trace_id=f"{run_id}-doctors-rollback",
    )

    cms_metrics = evaluate_detection(
        paths["dirty_cms_hospitals"],
        paths["dirty_cms_hospital_labels"],
        dataset_name="hospitals",
    )
    cms_readiness = hospital_readiness(
        dataset_name="hospitals",
        dataset_path=paths["clean_cms_hospitals"],
        store=store,
        audit_store=audit,
        trace_id=f"{run_id}-cms-readiness",
    )
    clean_cms_hospitals = load_csv(str(paths["clean_cms_hospitals"]))
    cms_quality_report = quality_report(clean_cms_hospitals, dataset_name="hospitals")
    cms_version = store.publish(
        dataset_name="hospitals",
        df=clean_cms_hospitals,
        quality_report=cms_quality_report,
        approved=True,
    )
    cms_regression = consumer_regression(
        dataset_name="hospitals",
        dataset_path=paths["clean_cms_hospitals"],
        store=store,
        consumer_agent="hospital_registry_consumer",
        audit_store=audit,
        trace_id=f"{run_id}-cms-regression",
    )
    cms_dirty_governance = start_hospital_governance(
        thread_id=f"{run_id}-cms-governance",
        dataset_name="hospitals",
        dataset_path=paths["dirty_cms_hospitals"],
        checkpoint_path=checkpoint_path,
        store=store,
        audit_store=audit,
        trace_id=f"{run_id}-cms-governance",
    )

    service_request_metrics = evaluate_detection(
        paths["dirty_service_requests"],
        paths["dirty_service_request_labels"],
        dataset_name="service_requests",
    )
    clean_service_requests = load_csv(str(paths["clean_service_requests"]))
    service_request_quality_report = quality_report(
        clean_service_requests,
        dataset_name="service_requests",
    )
    service_request_version = store.publish(
        dataset_name="service_requests",
        df=clean_service_requests,
        quality_report=service_request_quality_report,
        approved=True,
    )
    service_request_regression = consumer_regression(
        dataset_name="service_requests",
        dataset_path=paths["clean_service_requests"],
        store=store,
        consumer_agent="service_request_consumer",
        audit_store=audit,
        trace_id=f"{run_id}-service-requests-regression",
    )
    cross_domain_regression = _cross_domain_consumer_regression_summary(
        {
            "doctors": regression,
            "hospitals": cms_regression,
            "service_requests": service_request_regression,
        }
    )

    audit_events = audit.list_events(limit=200)
    doctor_thread_audit_events = audit.list_events(
        thread_id=doctor_governance_thread_id,
        limit=20,
    )
    gates = {
        "unapproved_publish_blocked": unapproved_publish_blocked,
        "doctor_dirty_readiness_blocks_release": readiness["decision"] == "governance_required",
        "governance_checkpoint_restored": saved_state["status"] == "awaiting_approval",
        "approved_governance_published": governance_final["status"] == "published",
        "consumer_regression_passed": regression["release_gate_passed"] is True,
        "downstream_hospital_retrieval_passed": (
            downstream_regression["release_gate_passed"] is True
        ),
        "unapproved_rollback_did_not_switch_active": (
            rollback_dry_run["rollback_executed"] is False
            and rollback_dry_run["active_version_after"]["version_id"]
            == governance_final["version"]["version_id"]
        ),
        "approved_rollback_switched_active": (
            rollback_approved["rollback_executed"] is True
            and rollback_approved["active_version_after"]["version_id"]
            == baseline_version.version_id
        ),
        "cms_public_sample_is_clean_candidate": cms_readiness["safe_for_hospital_agent"] is True,
        "cms_public_version_published": cms_version.dataset_name == "hospitals",
        "cms_consumer_regression_passed": cms_regression["release_gate_passed"] is True,
        "cms_dirty_sample_requires_manual_review": (
            cms_dirty_governance["status"] == "manual_review_required"
        ),
        "service_request_public_sample_is_clean_candidate": service_request_quality_report[
            "passes_quality_gate"
        ]
        is True,
        "service_request_public_version_published": (
            service_request_version.dataset_name == "service_requests"
        ),
        "service_request_consumer_regression_passed": (
            service_request_regression["release_gate_passed"] is True
        ),
        "cross_domain_consumer_regression_passed": (
            cross_domain_regression["release_gate_passed"] is True
        ),
        "public_source_manifest_valid": source_manifest["status"] == "ok",
        "trace_events_recorded": len(audit_events) >= 7,
        "governance_thread_audit_recorded": {
            event["operation"] for event in doctor_thread_audit_events
        }
        >= {"governance_start", "governance_resume"},
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
        },
        "gates": gates,
        "doctor_dataset": {
            "source": str(paths["clean_doctors"]),
            "dirty_candidate": str(paths["dirty_doctors"]),
            "detection_metrics": doctor_metrics.model_dump(),
            "readiness_decision": readiness["decision"],
            "governance_start_status": governance_start["status"],
            "checkpoint_status": saved_state["status"],
            "governance_final_status": governance_final["status"],
            "published_version_id": governance_final["version"]["version_id"],
            "consumer_regression_status": regression["status"],
            "consumer_regression_metrics": regression["metrics"],
            "downstream_hospital_agent_status": downstream_regression["status"],
            "downstream_hospital_agent_metrics": downstream_regression["metrics"],
            "downstream_hospital_agent_report": downstream_regression["outputs"],
            "rollback_dry_run_status": rollback_dry_run["status"],
            "rollback_final_status": rollback_approved["status"],
            "active_version_after_rollback": rollback_approved["active_version_after"],
        },
        "cms_public_dataset": {
            "source": str(paths["clean_cms_hospitals"]),
            "dirty_candidate": str(paths["dirty_cms_hospitals"]),
            "detection_metrics": cms_metrics.model_dump(),
            "readiness_decision": cms_readiness["decision"],
            "quality_report": cms_quality_report,
            "published_version_id": cms_version.version_id,
            "consumer_regression_status": cms_regression["status"],
            "consumer_regression_metrics": cms_regression["metrics"],
            "dirty_governance_status": cms_dirty_governance["status"],
            "unresolved_issue_count": cms_dirty_governance.get("unresolved_issue_count", 0),
        },
        "service_request_dataset": {
            "source": str(paths["clean_service_requests"]),
            "dirty_candidate": str(paths["dirty_service_requests"]),
            "source_type": "NYC Open Data 311 Service Requests public API",
            "quality_report": service_request_quality_report,
            "detection_metrics": service_request_metrics.model_dump(),
            "published_version_id": service_request_version.version_id,
            "published_version_checksum": service_request_version.checksum,
            "consumer_regression_status": service_request_regression["status"],
            "consumer_regression_metrics": service_request_regression["metrics"],
        },
        "cross_domain_consumer_regression": cross_domain_regression,
        "source_manifest": source_manifest,
        "trace": {
            "event_count": len(audit_events),
            "trace_ids": sorted({event["trace_id"] for event in audit_events}),
            "governance_thread_id": doctor_governance_thread_id,
            "governance_thread_event_count": len(doctor_thread_audit_events),
            "governance_thread_events": doctor_thread_audit_events,
            "events": audit_events,
        },
        "interview_boundary": {
            "can_say": [
                "本地可复现的数据治理闭环",
                "公开数据候选集留痕后进入质量门禁",
                "人工审批后发布，未审批写入和回滚被拦截",
                "同一套规则/版本底座已覆盖非医院公开服务请求数据",
                "通用 consumer regression 已覆盖医生、医院公开样本和服务请求三个数据域",
                "医院 Agent 与 DataOps Agent 通过 HTTP/JSON 契约协作",
                "DataOps 发布版本可转成医院 Agent 知识包并通过本地检索回归",
            ],
            "cannot_say": [
                "真实医院生产上线",
                "真实患者流量或业务 ROI",
                "Qwen 参与本轮数据修复",
                "ECS 公网压测已完成",
            ],
        },
    }

    _write_report(Path(report_json), report)
    _write_report_markdown(Path(report_md), report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the local DataOps Agent demo.")
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    report = run_local_demo(
        report_json=args.report_json,
        report_md=args.report_md,
        runtime_root=args.runtime_root,
        run_id=args.run_id,
    )
    print(json.dumps(_stdout_summary(report), ensure_ascii=False, indent=2))
    if report["status"] != "ok":
        raise SystemExit(1)


def _demo_paths() -> dict[str, Path]:
    return {
        "clean_doctors": PROJECT_ROOT / "data" / "raw" / "clean_dev.csv",
        "dirty_doctors": PROJECT_ROOT / "data" / "derived" / "dirty_dev.csv",
        "dirty_doctor_labels": PROJECT_ROOT / "data" / "derived" / "dirty_dev_labels.json",
        "clean_cms_hospitals": PROJECT_ROOT
        / "data"
        / "imported"
        / "cms_hospitals_normalized_sample.csv",
        "dirty_cms_hospitals": PROJECT_ROOT / "data" / "derived" / "cms_hospitals_dirty_sample.csv",
        "dirty_cms_hospital_labels": PROJECT_ROOT
        / "data"
        / "derived"
        / "cms_hospitals_dirty_sample_labels.json",
        "clean_service_requests": PROJECT_ROOT
        / "data"
        / "imported"
        / "nyc_311_service_requests_sample.csv",
        "dirty_service_requests": PROJECT_ROOT
        / "data"
        / "derived"
        / "nyc_311_service_requests_dirty_sample.csv",
        "dirty_service_request_labels": PROJECT_ROOT
        / "data"
        / "derived"
        / "nyc_311_service_requests_dirty_sample_labels.json",
    }


def _require_files(paths: dict[str, Path]) -> None:
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Demo input files are missing: {', '.join(missing)}")


def _unapproved_publish_is_blocked(store: VersionStore, df) -> bool:
    try:
        store.publish(
            dataset_name="doctors",
            df=df,
            quality_report=quality_report(df, dataset_name="doctors"),
            approved=False,
        )
    except PermissionError:
        return True
    return False


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_report_markdown(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_markdown_report(report), encoding="utf-8")


def _markdown_report(report: dict[str, Any]) -> str:
    doctor = report["doctor_dataset"]
    cms = report["cms_public_dataset"]
    service_requests = report["service_request_dataset"]
    cross_domain = report["cross_domain_consumer_regression"]
    gates = report["gates"]
    lines = [
        "# DataOps Agent 本地演示报告",
        "",
        f"- 运行 ID：`{report['run_id']}`",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 结果：`{report['status']}`",
        "- 范围：本地离线演示，未启用 ECS，未调用 Qwen，非生产流量。",
        "",
        "## 主链路结果",
        "",
        "| 环节 | 结果 | 证据 |",
        "|---|---|---|",
        (
            "| 医生脏数据检测 | "
            f"F1={doctor['detection_metrics']['f1']} | "
            f"TP={doctor['detection_metrics']['true_positive']}，"
            f"FP={doctor['detection_metrics']['false_positive']}，"
            f"FN={doctor['detection_metrics']['false_negative']} |"
        ),
        (f"| 医院 Agent readiness | {doctor['readiness_decision']} | 脏数据不会直接发布 |"),
        (
            "| LangGraph 审批恢复 | "
            f"{doctor['checkpoint_status']} -> {doctor['governance_final_status']} | "
            f"发布版本 `{doctor['published_version_id']}` |"
        ),
        (
            "| 发布前 consumer canary | "
            f"{doctor['consumer_regression_status']} | "
            f"case={doctor['consumer_regression_metrics']['candidate_case_count']} |"
        ),
        (
            "| 医院 Agent 下游检索回归 | "
            f"{doctor['downstream_hospital_agent_status']} | "
            f"top1={doctor['downstream_hospital_agent_metrics']['top1_accuracy']}，"
            f"hit@k={doctor['downstream_hospital_agent_metrics']['hit_at_k']}，"
            f"leak={doctor['downstream_hospital_agent_metrics']['forbidden_doc_leak_count']} |"
        ),
        (
            "| 回滚演练 | "
            f"{doctor['rollback_dry_run_status']} -> {doctor['rollback_final_status']} | "
            "未审批不切换，审批后切换 |"
        ),
        (
            "| CMS 公开样本检测 | "
            f"F1={cms['detection_metrics']['f1']} | "
            f"canary={cms['consumer_regression_status']}，"
            f"脏样本治理状态 `{cms['dirty_governance_status']}` |"
        ),
        (
            "| NYC 311 服务请求数据 | "
            f"F1={service_requests['detection_metrics']['f1']} | "
            f"公开样本 rows={service_requests['quality_report']['profile']['row_count']}，"
            f"版本 `{service_requests['published_version_id']}`，"
            f"canary={service_requests['consumer_regression_status']} |"
        ),
        (
            "| 跨数据域 consumer canary | "
            f"{cross_domain['status']} | "
            f"datasets={cross_domain['dataset_count']}，"
            f"passed={cross_domain['passed_dataset_count']} |"
        ),
        (
            "| 跨 Agent 审计 | "
            f"{report['trace']['event_count']} events | "
            f"{len(report['trace']['trace_ids'])} traces |"
        ),
        (
            "| 公开数据源目录 | "
            f"{report['source_manifest']['status']} | "
            f"sources={report['source_manifest']['source_count']}，"
            f"issues={report['source_manifest']['issue_count']} |"
        ),
        "",
        "## 安全门禁",
        "",
    ]
    lines.extend(f"- {name}: `{value}`" for name, value in gates.items())
    lines.extend(
        [
            "",
            "## 面试边界",
            "",
            (
                "- 可以说：本地可复现、公开数据候选集、审批发布、版本回滚、"
                "跨数据域质量治理、医院 Agent 联动、下游检索回归、trace/thread 审计。"
            ),
            (
                "- 不能说：真实医院生产上线、真实患者流量、真实业务 ROI、"
                "ECS 压测已完成、本轮 Qwen 参与修复。"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _stdout_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report["status"],
        "run_id": report["run_id"],
        "doctor_detection_f1": report["doctor_dataset"]["detection_metrics"]["f1"],
        "cms_detection_f1": report["cms_public_dataset"]["detection_metrics"]["f1"],
        "service_request_detection_f1": report["service_request_dataset"]["detection_metrics"][
            "f1"
        ],
        "gates_passed": all(report["gates"].values()),
        "downstream_hospital_hit_at_k": report["doctor_dataset"][
            "downstream_hospital_agent_metrics"
        ]["hit_at_k"],
        "trace_event_count": report["trace"]["event_count"],
        "cross_domain_consumer_regression_status": report["cross_domain_consumer_regression"][
            "status"
        ],
        "report_json": report["outputs"]["report_json"],
        "report_md": report["outputs"]["report_md"],
    }


def _safe_run_id(value: str) -> str:
    return "demo-" + "".join(char if char.isalnum() else "-" for char in value).strip("-")


def _cross_domain_consumer_regression_summary(
    results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    dataset_results = {
        dataset_name: {
            "status": result["status"],
            "release_gate_passed": result["release_gate_passed"],
            "candidate_case_count": result["metrics"]["candidate_case_count"],
            "baseline_key_coverage": result["metrics"]["baseline_key_coverage"],
            "quality_issue_count": result["metrics"]["quality_issue_count"],
        }
        for dataset_name, result in results.items()
    }
    passed_count = sum(1 for item in dataset_results.values() if item["release_gate_passed"])
    release_gate_passed = passed_count == len(dataset_results)
    return {
        "contract": "dataops_consumer_v1",
        "status": "passed" if release_gate_passed else "failed",
        "release_gate_passed": release_gate_passed,
        "dataset_count": len(dataset_results),
        "passed_dataset_count": passed_count,
        "results": dataset_results,
    }


if __name__ == "__main__":
    main()
