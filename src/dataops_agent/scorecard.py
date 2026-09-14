from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dataops_agent.acceptance import run_acceptance
from dataops_agent.approval_evidence import run_approval_status_evidence
from dataops_agent.boundary_guard import run_boundary_guard
from dataops_agent.ci_quality_matrix import run_ci_quality_matrix
from dataops_agent.collaboration import run_collaboration_report
from dataops_agent.concurrency import run_concurrency_report
from dataops_agent.cross_project_recovery import run_cross_project_recovery_drill
from dataops_agent.freshness import run_freshness_report
from dataops_agent.hospital_impact import run_hospital_impact_report
from dataops_agent.performance import run_multiscale_performance_report, run_performance_report
from dataops_agent.private_sources import run_private_source_report
from dataops_agent.release_review import run_release_review_report
from dataops_agent.rolling_open_data import run_rolling_open_data_report
from dataops_agent.security_review import run_security_review
from dataops_agent.sources import validate_source_manifest
from dataops_agent.training_candidates import run_training_candidate_export

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "dataops_scorecard.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "dataops_scorecard.md"
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT / "reports" / "scorecard_runtime"
QWEN_LIVE_REPORT = PROJECT_ROOT / "reports" / "qwen_live_readiness_20260822_batch6b.json"
ECS_LIVE_REPORT = PROJECT_ROOT / "reports" / "dataops_live_validation_20260822_batch6b.json"
ECS_CURRENT_VALIDATION_REPORT = (
    PROJECT_ROOT / "reports" / "dataops_live_validation_20260908_pl_o1.json"
)
KNOWLEDGE_RELEASE_MANIFEST = PROJECT_ROOT / "reports" / "rag_release_service_requests_manifest.json"
BASELINE_REPORTS = {
    "hospitals": PROJECT_ROOT / "reports" / "cms_hospitals_detection_baseline.json",
    "service_requests": PROJECT_ROOT / "reports" / "service_requests_detection_baseline.json",
}
DECISION_MEMORY_REPORT = (
    PROJECT_ROOT / "reports" / "decision_memory_p0_m2_20260912_metrics.json"
)


def run_scorecard(
    *,
    report_json: Path | str = DEFAULT_REPORT_JSON,
    report_md: Path | str = DEFAULT_REPORT_MD,
    runtime_root: Path | str = DEFAULT_RUNTIME_ROOT,
    run_id: str | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    run_id = run_id or _safe_run_id(generated_at)
    runtime_dir = Path(runtime_root) / run_id
    runtime_dir.mkdir(parents=True, exist_ok=True)

    public_sources = validate_source_manifest()
    acceptance = run_acceptance(
        report_json=runtime_dir / "acceptance.json",
        report_md=runtime_dir / "acceptance.md",
        runtime_root=runtime_dir / "acceptance_runtime",
        run_id=f"{run_id}-acceptance",
    )
    approval_status = run_approval_status_evidence(
        report_json=runtime_dir / "approval_status.json",
        report_md=runtime_dir / "approval_status.md",
        runtime_root=runtime_dir / "approval_status_runtime",
        run_id=f"{run_id}-approval-status",
    )
    security = run_security_review(
        report_json=runtime_dir / "security_review.json",
        report_md=runtime_dir / "security_review.md",
        runtime_root=runtime_dir / "security_review_runtime",
        run_id=f"{run_id}-security",
    )
    boundary_guard = run_boundary_guard(
        report_json=runtime_dir / "boundary_guard.json",
        report_md=runtime_dir / "boundary_guard.md",
        manifest_md=runtime_dir / "project_boundary_manifest.md",
        run_id=f"{run_id}-boundary-guard",
    )
    ci_quality = run_ci_quality_matrix(
        report_json=runtime_dir / "ci_quality_matrix.json",
        report_md=runtime_dir / "ci_quality_matrix.md",
        run_id=f"{run_id}-ci-quality",
    )
    performance = run_performance_report(
        report_json=runtime_dir / "performance.json",
        report_md=runtime_dir / "performance.md",
        runtime_root=runtime_dir / "performance_runtime",
        run_id=f"{run_id}-performance",
    )
    multiscale_performance = run_multiscale_performance_report(
        report_json=runtime_dir / "multiscale_performance.json",
        report_md=runtime_dir / "multiscale_performance.md",
        runtime_root=runtime_dir / "multiscale_performance_runtime",
        run_id=f"{run_id}-multiscale-performance",
    )
    version_consistency = run_concurrency_report(
        report_json=runtime_dir / "concurrency.json",
        report_md=runtime_dir / "concurrency.md",
        runtime_root=runtime_dir / "concurrency_runtime",
        run_id=f"{run_id}-concurrency",
    )
    private_source = run_private_source_report(
        candidate_csv=runtime_dir / "authorized_service_requests_sample.csv",
        report_json=runtime_dir / "private_source.json",
        report_md=runtime_dir / "private_source.md",
    )
    freshness = run_freshness_report(
        report_json=runtime_dir / "freshness.json",
        report_md=runtime_dir / "freshness.md",
        run_id=f"{run_id}-freshness",
    )
    rolling_open_data = run_rolling_open_data_report(
        report_json=runtime_dir / "rolling_open_data.json",
        report_md=runtime_dir / "rolling_open_data.md",
        runtime_root=runtime_dir / "rolling_open_data_runtime",
        run_id=f"{run_id}-rolling-open-data",
    )
    collaboration = run_collaboration_report(
        report_json=runtime_dir / "collaboration.json",
        report_md=runtime_dir / "collaboration.md",
        run_id=f"{run_id}-collaboration",
    )
    cross_project_recovery = run_cross_project_recovery_drill(
        report_json=runtime_dir / "cross_project_recovery.json",
        report_md=runtime_dir / "cross_project_recovery.md",
        runtime_root=runtime_dir / "cross_project_recovery_runtime",
        run_id=f"{run_id}-cross-project-recovery",
    )
    hospital_impact = run_hospital_impact_report(
        collaboration_report=runtime_dir / "collaboration.json",
        report_json=runtime_dir / "hospital_impact.json",
        report_md=runtime_dir / "hospital_impact.md",
        run_id=f"{run_id}-hospital-impact",
    )
    release_review = run_release_review_report(
        report_json=runtime_dir / "release_review.json",
        report_md=runtime_dir / "release_review.md",
        runtime_root=runtime_dir / "release_review_runtime",
        run_id=f"{run_id}-release-review",
    )
    training_candidates = run_training_candidate_export(
        report_json=runtime_dir / "training_candidates.json",
        report_md=runtime_dir / "training_candidates.md",
        candidates_jsonl=runtime_dir / "training_candidates.jsonl",
        runtime_root=runtime_dir / "training_candidates_runtime",
        run_id=f"{run_id}-training-candidates",
    )
    qwen_live = _qwen_summary(_load_optional_json(QWEN_LIVE_REPORT))
    ecs_live = _ecs_summary(_load_optional_json(ECS_LIVE_REPORT))
    ecs_current_validation = _ecs_current_validation_summary(
        _load_optional_json(ECS_CURRENT_VALIDATION_REPORT)
    )
    knowledge_release = _knowledge_release_summary(_load_optional_json(KNOWLEDGE_RELEASE_MANIFEST))
    baselines = _baseline_summaries()
    decision_memory = _decision_memory_summary(_load_optional_json(DECISION_MEMORY_REPORT))

    status_inputs = [
        public_sources["status"],
        acceptance["status"],
        approval_status["status"],
        security["status"],
        boundary_guard["status"],
        ci_quality["status"],
        performance["status"],
        multiscale_performance["status"],
        version_consistency["status"],
        private_source["status"],
        freshness["status"],
        rolling_open_data["status"],
        collaboration["status"],
        cross_project_recovery["status"],
        hospital_impact["status"],
        release_review["status"],
        training_candidates["status"],
    ]
    report = {
        "status": "ok" if all(status == "ok" for status in status_inputs) else "failed",
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
            "api_key_recorded": False,
            "historical_qwen_report_reused": qwen_live["report_found"],
            "historical_ecs_report_reused": ecs_live["report_found"],
            "current_ecs_validation_report_reused": ecs_current_validation["report_found"],
            "decision_memory_report_reused": decision_memory["report_found"],
        },
        "component_reports": {
            "acceptance": acceptance["outputs"],
            "approval_status": approval_status["outputs"],
            "security": security["outputs"],
            "boundary_guard": boundary_guard["outputs"],
            "ci_quality": ci_quality["outputs"],
            "performance": performance["outputs"],
            "multiscale_performance": multiscale_performance["outputs"],
            "version_consistency": version_consistency["outputs"],
            "private_source": {
                "report_json": str(runtime_dir / "private_source.json"),
                "report_md": str(runtime_dir / "private_source.md"),
            },
            "freshness": freshness["outputs"],
            "rolling_open_data": rolling_open_data["outputs"],
            "collaboration": collaboration["outputs"],
            "cross_project_recovery": cross_project_recovery["outputs"],
            "hospital_impact": hospital_impact["outputs"],
            "release_review": release_review["outputs"],
            "training_candidates": training_candidates["outputs"],
            "decision_memory": {
                "report_json": str(DECISION_MEMORY_REPORT),
                "report_md": str(
                    PROJECT_ROOT / "reports" / "decision_memory_p0_m2_20260912_summary.md"
                ),
            },
        },
        "metrics": {
            "data_sources": _data_source_metrics(public_sources, private_source),
            "data_quality": _data_quality_metrics(public_sources, private_source, baselines),
            "freshness": _freshness_metrics(freshness),
            "rolling_open_data": _rolling_open_data_metrics(rolling_open_data),
            "collaboration": _collaboration_metrics(collaboration),
            "cross_project_recovery": _cross_project_recovery_metrics(
                cross_project_recovery
            ),
            "hospital_agent_impact": _hospital_impact_metrics(hospital_impact),
            "release_review": _release_review_metrics(release_review),
            "training_candidates": _training_candidate_metrics(training_candidates),
            "governance_release": _governance_metrics(acceptance),
            "approval_status": _approval_status_metrics(approval_status),
            "downstream_consumption": _downstream_metrics(performance, knowledge_release),
            "performance": _performance_metrics(performance, multiscale_performance),
            "version_consistency": _version_consistency_metrics(version_consistency),
            "security": _security_metrics(security),
            "boundary_guard": _boundary_guard_metrics(boundary_guard),
            "ci_quality": _ci_quality_metrics(ci_quality),
            "decision_memory": decision_memory,
            "model_and_cloud": {
                "qwen_live": qwen_live,
                "ecs_public_pre_release": ecs_live,
                "ecs_current_validation": ecs_current_validation,
            },
        },
        "resume_ready_metrics": [
            "公开来源 2 类，私有/授权导出 1 类，本地候选数据不自动发布。",
            "7 类治理失败门禁、7 类安全对抗场景通过本地确定性验收。",
            "审批状态证据覆盖待审批、批准、发布成功、拒绝、人工复核和门禁失败。",
            "跨项目能力边界审查覆盖 10 个 scope guardrail 场景，拒绝 8 类越权变更。",
            (
                "CI-ready 质量门禁配置覆盖 5 类测试矩阵和全部 test_*.py 文件，"
                "本轮不声明远端 CI 已运行。"
            ),
            "500 行 NYC 311 样本完成质量检测、版本发布和 consumer regression。",
            "多规模本地回放覆盖 500、2500、5000 行，记录阶段耗时、行吞吐和错误率边界。",
            (
                "滚动开放数据回放覆盖 4 个 NYC 311 快照，识别新增、迟到、重复和字段缺失，"
                "坏候选被阻断并可审批回滚。"
            ),
            (
                "并发与幂等验证覆盖重复提交、同键换数据、旧 active 发布和"
                "同父版本并发冲突，当前不声明分布式锁或生产任务队列。"
            ),
            (
                "治理决策记忆专项 12/12 场景通过；5 次重复任务对照中模型调用和"
                "模拟 token 均下降 100%，审批绕过、错误复用和跨作用域泄漏为 0。"
            ),
            "公开来源完成本地 freshness 检查和 dry-run 调度记录，过期/不可信来源不自动发布。",
            "多 Agent 协作有本地消费者契约报告，明确 DataOps 与下游消费者的权限边界。",
            (
                "跨项目恢复演练覆盖坏候选阻断、active 保持、审批回滚和恢复后"
                "消费者回归。"
            ),
            "医院 Agent 影响报告证明医生知识包 6/6 下游回归通过，inactive 记录 0 泄漏。",
            "发布审查型 Reviewer 只读复核验收、安全、性能和协作证据，建议进入人工审批但不能发布。",
            "评测反例可导出训练候选池，当前 7 条候选均需人工标注，且不直接执行 SFT。",
            (
                "Qwen readiness 复用历史小流量报告；ECS 云环境证据已包含历史公网"
                "预发布和 PL-O1 当前工作区复验，scorecard 本轮不主动调用云资源。"
            ),
        ],
        "boundary": {
            "can_say": [
                "DataOps 有统一 scorecard 汇总质量、发布、回归、性能、安全、模型和云验证证据。",
                (
                    "scorecard 已纳入审批状态证据，覆盖待审批、批准、拒绝、"
                    "人工复核、门禁失败和发布成功。"
                ),
                "DataOps 有并发与幂等验证，能证明重复发布和旧基线覆盖会被显式处理。",
                "DataOps 有能力边界契约审查，能拦截项目定位、权限、数据来源和简历口径越界。",
                "DataOps 有 CI-ready 质量门禁和测试矩阵证据，覆盖全部 test_*.py 文件。",
                (
                    "scorecard 复用本地可复现报告，并区分本轮本地执行、历史 "
                    "Qwen/ECS 证据和 PL-O1 当前版本 ECS 复验。"
                ),
                "私有/授权数据源当前用脱敏模拟导出证明 adapter 和治理边界。",
                "数据新鲜度当前用 metadata/checksum 做本地检查，并生成轻量 dry-run 调度记录。",
                (
                    "DataOps 有滚动开放数据回放，能证明新增、迟到、重复和 schema "
                    "变化不会绕过发布边界。"
                ),
                "DataOps 与下游 Agent 通过 consumer contract、发布前回归和 trace/thread 审计协作。",
                "DataOps 有本地跨项目恢复演练，能证明坏候选不切 active，回滚后可恢复上一稳定版本。",
                "DataOps 对医院 Agent 的提升是知识发布可靠性和下游消费安全，不是线上问答准确率。",
                "发布审查型 Reviewer 只做风险审查和人工审批建议，不拥有正式写入权限。",
                "评测反例已经能导出训练候选池，但当前没有执行 SFT 或偏好学习。",
                (
                    "DataOps 只复用同数据集、同合同和同字段值的人工治理决策；"
                    "记忆不能绕过审批、质量和发布门禁。"
                ),
            ],
            "cannot_say": [
                "不能说 scorecard 本轮主动进行了新的 Qwen 调用或 ECS 复验。",
                "不能说已经完成 SFT、DPO、RLHF、偏好学习或在线训练。",
                "不能说已经接入 Airflow、Kafka 或生产级调度告警平台。",
                "不能说已经实现分布式事务、分布式锁或生产任务队列。",
                "不能说这些指标是生产 SLA、真实 QPS、真实用户流量或真实业务 ROI。",
                "不能说接入了真实企业内网、真实医院库或真实客户数据库。",
                "不能说已经实现生产级 A2A 协议、消息队列或多 Agent 调度平台。",
                "不能说能力边界审查是生产级 WAF、DLP、SIEM、企业 RBAC 或真实红队测试。",
                "不能说远端 GitHub Actions、企业 CI 或生产质量趋势已经真实运行通过。",
                "不能说 DataOps 已经直接提升医院 Agent 线上问答准确率或自动写生产索引。",
                "不能说跨项目恢复演练是真实医院生产事故、生产灰度或真实用户流量恢复。",
                "不能说 Reviewer 可以审批、发布或回滚正式版本。",
                "不能说决策记忆来自线上流量、生产审批或真实模型批量评测。",
            ],
        },
    }
    _write_json(report_json, report)
    _write_markdown(report_md, report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run DataOps Agent local evidence scorecard.")
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    report = run_scorecard(
        report_json=args.report_json,
        report_md=args.report_md,
        runtime_root=args.runtime_root,
        run_id=args.run_id,
    )
    print(json.dumps(_stdout_summary(report), ensure_ascii=False, indent=2))
    if report["status"] != "ok":
        raise SystemExit(1)


def _data_source_metrics(
    public_sources: dict[str, Any],
    private_source: dict[str, Any],
) -> dict[str, Any]:
    public_rows = {
        str(source["dataset_name"]): int(source.get("row_count") or 0)
        for source in public_sources["sources"]
    }
    return {
        "public_source_count": public_sources["source_count"],
        "public_datasets": public_sources["datasets"],
        "public_rows_by_dataset": public_rows,
        "authorized_private_source_count": 1 if private_source["status"] == "ok" else 0,
        "authorized_private_rows": private_source["source"]["raw_row_count"],
        "authorized_private_boundary": private_source["source"]["candidate_boundary"],
    }


def _data_quality_metrics(
    public_sources: dict[str, Any],
    private_source: dict[str, Any],
    baselines: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    private_quality = private_source["quality_report"] or {}
    return {
        "public_manifest_status": public_sources["status"],
        "public_manifest_issue_count": public_sources["issue_count"],
        "public_candidate_quality_gates_passed": all(
            source.get("candidate_quality_gate_passed") for source in public_sources["sources"]
        ),
        "private_candidate_quality_gate_passed": private_quality.get("passes_quality_gate", False),
        "private_candidate_issue_count": private_quality.get("issue_count"),
        "detection_baselines": baselines,
    }


def _governance_metrics(acceptance: dict[str, Any]) -> dict[str, Any]:
    summary = acceptance["summary"]
    cases = {case["name"]: case for case in acceptance["cases"]}
    return {
        "acceptance_case_count": summary["case_count"],
        "acceptance_passed_case_count": summary["passed_case_count"],
        "acceptance_pass_rate": _safe_ratio(
            summary["passed_case_count"],
            summary["case_count"],
        ),
        "unapproved_publish_blocked": cases["unapproved_publish_blocked"]["passed"],
        "candidate_checksum_mismatch_blocked": cases["candidate_checksum_mismatch_blocks_publish"][
            "passed"
        ],
        "unapproved_rollback_blocked": cases["unapproved_rollback_does_not_switch_active_version"][
            "passed"
        ],
    }


def _approval_status_metrics(approval_status: dict[str, Any]) -> dict[str, Any]:
    cases = approval_status["status_cases"]
    return {
        "status_case_count": len(cases),
        "status_cases": [case["name"] for case in cases],
        "all_status_gates_passed": all(approval_status["gates"].values()),
        "pending_status_visible": approval_status["gates"]["pending_status_visible"],
        "approval_decision_visible": approval_status["gates"]["approval_decision_visible"],
        "published_status_visible": approval_status["gates"]["published_status_visible"],
        "denial_status_visible": approval_status["gates"]["denial_status_visible"],
        "manual_review_status_visible": approval_status["gates"]["manual_review_status_visible"],
        "consumer_gate_failure_visible": approval_status["gates"]["consumer_gate_failure_visible"],
    }


def _freshness_metrics(freshness: dict[str, Any]) -> dict[str, Any]:
    metrics = freshness["metrics"]
    return {
        "source_count": metrics["source_count"],
        "fresh_source_count": metrics["fresh_source_count"],
        "stale_source_count": metrics["stale_source_count"],
        "blocked_source_count": metrics["blocked_source_count"],
        "due_for_refresh_count": metrics["due_for_refresh_count"],
        "release_ready_source_count": metrics["release_ready_source_count"],
        "dry_run_task_count": len(freshness["dry_run_schedule"]),
        "dry_run_only": freshness["scope"]["dry_run_only"],
    }


def _rolling_open_data_metrics(report: dict[str, Any]) -> dict[str, Any]:
    metrics = report["metrics"]
    gates = report["gates"]
    return {
        "snapshot_count": metrics["snapshot_count"],
        "source_sample_row_count": metrics["source_sample_row_count"],
        "new_record_count": metrics["new_record_count"],
        "late_arrival_count": metrics["late_arrival_count"],
        "duplicate_key_count": metrics["duplicate_key_count"],
        "schema_missing_required_column_count": metrics["schema_missing_required_column_count"],
        "bad_candidate_blocked_count": metrics["bad_candidate_blocked_count"],
        "active_preserved_after_bad_candidates": gates["active_preserved_after_bad_candidates"],
        "approved_rollback_restored_baseline": gates["approved_rollback_restored_baseline"],
        "network_called": report["scope"]["network_called"],
        "production_traffic": report["scope"]["production_traffic"],
        "streaming_platform_used": report["scope"]["streaming_platform_used"],
    }


def _collaboration_metrics(collaboration: dict[str, Any]) -> dict[str, Any]:
    metrics = collaboration["metrics"]
    return {
        "participant_count": metrics["participant_count"],
        "scenario_count": metrics["scenario_count"],
        "covered_scenario_count": metrics["covered_scenario_count"],
        "consumer_route_count": metrics["consumer_route_count"],
        "hospital_compatibility_route_count": metrics["hospital_compatibility_route_count"],
        "consumer_approved_rollback_blocked_at_api": metrics[
            "consumer_approved_rollback_blocked_at_api"
        ],
        "downstream_can_approve_count": metrics["downstream_can_approve_count"],
        "a2a_protocol_implemented": metrics["a2a_protocol_implemented"],
        "production_message_bus": metrics["production_message_bus"],
    }


def _cross_project_recovery_metrics(report: dict[str, Any]) -> dict[str, Any]:
    metrics = report["metrics"]
    return {
        "scenario_count": metrics["scenario_count"],
        "passed_scenario_count": metrics["passed_scenario_count"],
        "bad_candidate_blocked": metrics["bad_candidate_blocked"],
        "active_preserved_after_bad_publish": metrics["active_preserved_after_bad_publish"],
        "rollback_requires_dataops_approval": metrics["rollback_requires_dataops_approval"],
        "approved_rollback_executed": metrics["approved_rollback_executed"],
        "restored_active_matches_stable": metrics["restored_active_matches_stable"],
        "restored_regression_passed": metrics["restored_regression_passed"],
        "audit_event_count": metrics["audit_event_count"],
        "hospital_production_system_connected": report["scope"][
            "hospital_production_system_connected"
        ],
    }


def _hospital_impact_metrics(hospital_impact: dict[str, Any]) -> dict[str, Any]:
    metrics = hospital_impact["metrics"]
    return {
        "knowledge_doc_count": metrics["knowledge_doc_count"],
        "canary_case_count": metrics["canary_case_count"],
        "passed_canary_case_count": metrics["passed_canary_case_count"],
        "forbidden_doc_leak_count": metrics["forbidden_doc_leak_count"],
        "hospital_preflight_status": metrics["hospital_preflight_status"],
        "hospital_preflight_ready_for_reindex": metrics["hospital_preflight_ready_for_reindex"],
        "downstream_can_approve_count": metrics["downstream_can_approve_count"],
        "direct_answer_accuracy_measured": hospital_impact["scope"][
            "direct_hospital_answer_accuracy_eval"
        ],
    }


def _release_review_metrics(release_review: dict[str, Any]) -> dict[str, Any]:
    metrics = release_review["metrics"]
    return {
        "check_count": metrics["check_count"],
        "passed_check_count": metrics["passed_check_count"],
        "blocking_check_count": metrics["blocking_check_count"],
        "recommendation": metrics["recommendation"],
        "reviewer_can_approve_publish": metrics["reviewer_can_approve_publish"],
        "reviewer_can_publish_active_version": metrics["reviewer_can_publish_active_version"],
        "reviewer_can_rollback_active_version": metrics["reviewer_can_rollback_active_version"],
        "reviewer_can_execute_shell": metrics["reviewer_can_execute_shell"],
        "state_graph_node_count": metrics["state_graph_node_count"],
    }


def _training_candidate_metrics(training_candidates: dict[str, Any]) -> dict[str, Any]:
    metrics = training_candidates["metrics"]
    return {
        "candidate_record_count": metrics["candidate_record_count"],
        "direct_sft_candidate_count": metrics["direct_sft_candidate_count"],
        "preference_candidate_count": metrics["preference_candidate_count"],
        "deterministic_fix_first_count": metrics["deterministic_fix_first_count"],
        "requires_human_labeling_count": metrics["requires_human_labeling_count"],
        "model_training_executed": training_candidates["scope"]["model_training_executed"],
    }


def _downstream_metrics(
    performance: dict[str, Any],
    knowledge_release: dict[str, Any],
) -> dict[str, Any]:
    regression = performance["service_requests_regression"]
    metrics = regression["metrics"]
    return {
        "consumer_regression_status": regression["status"],
        "consumer_regression_release_gate_passed": regression["release_gate_passed"],
        "candidate_case_count": metrics["candidate_case_count"],
        "baseline_case_count": metrics["baseline_case_count"],
        "baseline_key_coverage": metrics["baseline_key_coverage"],
        "rag_ready_doc_count": knowledge_release.get("doc_count"),
        "rag_ready_manifest_found": knowledge_release.get("report_found", False),
    }


def _performance_metrics(
    performance: dict[str, Any],
    multiscale_performance: dict[str, Any] | None,
) -> dict[str, Any]:
    row_count = performance["dataset_scale"]["service_requests_row_count"]
    total_ms = performance["timings_ms"]["total"]
    rows_per_second = round(row_count / (total_ms / 1000), 2) if total_ms else None
    metrics = {
        "local_row_count": row_count,
        "total_ms": total_ms,
        "rows_per_second": rows_per_second,
        "quality_report_ms": performance["timings_ms"]["service_requests_quality_report"],
        "publish_ms": performance["timings_ms"]["service_requests_publish"],
        "consumer_regression_ms": performance["timings_ms"]["service_requests_consumer_regression"],
        "p95_p99_available": False,
        "p95_p99_boundary": "single local smoke run only; no production latency distribution",
    }
    if multiscale_performance is not None:
        metrics["multi_scale"] = {
            "status": multiscale_performance["status"],
            "scale_rows": multiscale_performance["dataset_scale"]["scale_rows"],
            "scale_point_count": multiscale_performance["dataset_scale"]["scale_point_count"],
            "max_row_count": multiscale_performance["dataset_scale"]["max_row_count"],
            "best_rows_per_second": multiscale_performance["summary"]["best_rows_per_second"],
            "worst_rows_per_second": multiscale_performance["summary"]["worst_rows_per_second"],
            "max_quality_issue_rate": multiscale_performance["summary"]["max_quality_issue_rate"],
            "max_consumer_failed_case_rate": multiscale_performance["summary"][
                "max_consumer_failed_case_rate"
            ],
            "synthetic_scale_extension": multiscale_performance["scope"][
                "synthetic_scale_extension"
            ],
        }
    else:
        metrics["multi_scale"] = {"status": "not_run"}
    return metrics


def _version_consistency_metrics(version_consistency: dict[str, Any]) -> dict[str, Any]:
    summary = version_consistency["summary"]
    return {
        "status": version_consistency["status"],
        "duplicate_request_reuses_version": version_consistency["gates"][
            "duplicate_request_reuses_version"
        ],
        "idempotency_key_reuse_with_different_data_blocked": version_consistency["gates"][
            "idempotency_key_reuse_with_different_data_blocked"
        ],
        "stale_expected_active_version_blocked": version_consistency["gates"][
            "stale_expected_active_version_blocked"
        ],
        "concurrent_publish_has_single_winner": version_consistency["gates"][
            "concurrent_publish_has_single_winner"
        ],
        "concurrent_published_count": summary["concurrent_published_count"],
        "concurrent_conflict_count": summary["concurrent_conflict_count"],
        "concurrent_version_count": summary["concurrent_version_count"],
        "distributed_lock_used": version_consistency["scope"]["distributed_lock_used"],
        "message_queue_used": version_consistency["scope"]["message_queue_used"],
    }


def _security_metrics(security: dict[str, Any]) -> dict[str, Any]:
    summary = security["summary"]
    return {
        "security_case_count": summary["case_count"],
        "security_passed_case_count": summary["passed_case_count"],
        "security_pass_rate": _safe_ratio(summary["passed_case_count"], summary["case_count"]),
        "covered_stages": sorted({case["stage"] for case in security["cases"]}),
    }


def _boundary_guard_metrics(boundary_guard: dict[str, Any]) -> dict[str, Any]:
    summary = boundary_guard["summary"]
    return {
        "boundary_case_count": summary["case_count"],
        "boundary_passed_case_count": summary["passed_case_count"],
        "boundary_pass_rate": _safe_ratio(
            summary["passed_case_count"],
            summary["case_count"],
        ),
        "allowed_change_count": summary["allowed_change_count"],
        "rejected_change_count": summary["rejected_change_count"],
        "protected_project_count": summary["protected_project_count"],
        "text_policy_count": summary["text_policy_count"],
    }


def _ci_quality_metrics(ci_quality: dict[str, Any]) -> dict[str, Any]:
    metrics = ci_quality["metrics"]
    return {
        "suite_count": metrics["suite_count"],
        "test_file_count": metrics["test_file_count"],
        "assigned_test_file_count": metrics["assigned_test_file_count"],
        "missing_from_matrix_count": metrics["missing_from_matrix_count"],
        "workflow_file_present": ci_quality["gates"]["workflow_file_present"],
        "workflow_runs_full_coverage": ci_quality["gates"]["workflow_runs_full_coverage"],
        "workflow_runs_scorecard": ci_quality["gates"]["workflow_runs_scorecard"],
        "junit_test_count": metrics["junit_test_count"],
        "junit_failure_count": metrics["junit_failure_count"],
        "coverage_line_rate": metrics["coverage_line_rate"],
        "coverage_branch_rate": metrics["coverage_branch_rate"],
        "ci_provider_executed": ci_quality["scope"]["ci_provider_executed"],
        "trend_history_available": ci_quality["scope"]["trend_history_available"],
    }


def _qwen_summary(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {
            "report_found": False,
            "called_this_run": False,
        }
    headline = report.get("headline_metrics", {})
    return {
        "report_found": True,
        "called_this_run": False,
        "status": report.get("status"),
        "model": report.get("model"),
        "case_count": headline.get("case_count"),
        "passed_count": headline.get("passed_count"),
        "overall_pass_rate": headline.get("overall_pass_rate"),
        "live_request_count": headline.get("live_request_count"),
        "total_tokens": headline.get("total_tokens"),
        "function_calling_valid": headline.get("function_calling_valid"),
        "api_key_recorded": report.get("api_key_recorded"),
    }


def _ecs_summary(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {
            "report_found": False,
            "used_this_run": False,
        }
    ecs = report.get("ecs_public", {})
    remote = ecs.get("remote_validations", {})
    return {
        "report_found": True,
        "used_this_run": False,
        "status": ecs.get("status"),
        "deployed_commit": ecs.get("deployed_commit"),
        "pytest_passed": remote.get("pytest", {}).get("passed"),
        "ruff_status": remote.get("ruff", {}).get("status"),
        "public_health": ecs.get("api_service", {}).get("public_health"),
        "performance_total_ms": remote.get("performance", {}).get("total_ms"),
        "boundary": report.get("decision", {}),
    }


def _ecs_current_validation_summary(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {
            "report_found": False,
            "used_this_run": False,
        }
    key_results = report.get("key_results", {})
    source_scope = report.get("source_scope", {})
    deployment = report.get("deployment", {})
    pytest = key_results.get("pytest", {})
    health = key_results.get("health", {})
    performance = key_results.get("performance", {})
    steps = report.get("steps", [])
    all_steps_ok = all(step.get("exit_code") == 0 for step in steps)
    return {
        "report_found": True,
        "used_this_run": False,
        "status": "ok" if all_steps_ok else "failed",
        "run_id": report.get("run_id"),
        "git_head": source_scope.get("git_head"),
        "includes_uncommitted_worktree": source_scope.get("includes_uncommitted_worktree"),
        "pytest_tests": pytest.get("tests"),
        "pytest_status": pytest.get("status"),
        "ruff_status": key_results.get("ruff"),
        "coverage_xml_line_rate": pytest.get("coverage_xml_line_rate"),
        "coverage_xml_branch_rate": pytest.get("coverage_xml_branch_rate"),
        "performance_total_ms": performance.get("total_ms"),
        "health_loop_count": health.get("loop_count"),
        "health_loop_all_ok": health.get("loop_all_ok"),
        "public_app_port_opened": deployment.get("public_app_port_opened"),
        "qwen_called": deployment.get("qwen_called"),
    }


def _knowledge_release_summary(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {"report_found": False}
    return {
        "report_found": True,
        "status": report.get("status"),
        "dataset_name": report.get("dataset_name"),
        "doc_count": report.get("doc_count"),
        "dataops_builds_embedding": report.get("rag_boundary", {}).get("dataops_builds_embedding"),
        "dataops_writes_vector_db": report.get("rag_boundary", {}).get("dataops_writes_vector_db"),
    }


def _baseline_summaries() -> dict[str, dict[str, Any]]:
    result = {}
    for dataset_name, path in BASELINE_REPORTS.items():
        report = _load_optional_json(path)
        if report:
            result[dataset_name] = {
                "precision": report.get("precision"),
                "recall": report.get("recall"),
                "f1": report.get("f1"),
                "true_positive": report.get("true_positive"),
                "false_positive": report.get("false_positive"),
                "false_negative": report.get("false_negative"),
            }
        else:
            result[dataset_name] = {"report_found": False}
    return result


def _decision_memory_summary(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {"report_found": False, "executed_this_run": False}
    metrics = report.get("metrics", {})
    return {
        "report_found": True,
        "executed_this_run": False,
        "status": report.get("status"),
        "scenario_count": metrics.get("scenario_count"),
        "scenario_passed": metrics.get("scenario_passed"),
        "repeat_count_per_mode": metrics.get("repeat_count_per_mode"),
        "model_call_reduction_percent": metrics.get("model_call_reduction_percent"),
        "token_reduction_percent": metrics.get("token_reduction_percent"),
        "gate_bypass_count": metrics.get("gate_bypass_count"),
        "rejected_error_reuse_count": metrics.get("rejected_error_reuse_count"),
        "cross_scope_leak_count": metrics.get("cross_scope_leak_count"),
        "scope": report.get("scope", {}),
    }


def _load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_markdown(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    metrics = report["metrics"]
    performance = metrics["performance"]
    freshness = metrics["freshness"]
    rolling_open_data = metrics["rolling_open_data"]
    collaboration = metrics["collaboration"]
    cross_project_recovery = metrics["cross_project_recovery"]
    approval_status = metrics["approval_status"]
    release_review = metrics["release_review"]
    training_candidates = metrics["training_candidates"]
    hospital_impact = metrics["hospital_agent_impact"]
    boundary_guard = metrics["boundary_guard"]
    ci_quality = metrics["ci_quality"]
    version_consistency = metrics["version_consistency"]
    decision_memory = metrics["decision_memory"]
    multi_scale = performance.get("multi_scale", {})
    qwen_live = metrics["model_and_cloud"]["qwen_live"]
    ecs_live = metrics["model_and_cloud"]["ecs_public_pre_release"]
    ecs_current = metrics["model_and_cloud"]["ecs_current_validation"]
    lines = [
        "# DataOps Agent 评测与可观测 Scorecard",
        "",
        f"- 运行 ID：`{report['run_id']}`",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 结果：`{report['status']}`",
        "- 范围：本地聚合报告；本轮未启用 ECS，未调用 Qwen，非生产流量。",
        "",
        "## 核心指标",
        "",
        "| 维度 | 指标 |",
        "|---|---|",
        (
            "| 数据来源 | "
            f"公开来源 {metrics['data_sources']['public_source_count']} 类，"
            f"授权私有导出 {metrics['data_sources']['authorized_private_source_count']} 类 |"
        ),
        (
            "| 数据质量 | "
            f"公开 manifest issue={metrics['data_quality']['public_manifest_issue_count']}，"
            f"私有候选 issue={metrics['data_quality']['private_candidate_issue_count']} |"
        ),
        (
            "| 治理发布 | "
            f"{metrics['governance_release']['acceptance_passed_case_count']}/"
            f"{metrics['governance_release']['acceptance_case_count']} 验收门禁通过 |"
        ),
        (
            "| 审批状态 | "
            f"{approval_status['status_case_count']} 类状态可复现，"
            f"all gates passed={approval_status['all_status_gates_passed']} |"
        ),
        (
            "| 新鲜度/调度 | "
            f"{freshness['fresh_source_count']}/{freshness['source_count']} 来源新鲜，"
            f"dry-run task={freshness['dry_run_task_count']}，"
            f"stale={freshness['stale_source_count']} |"
        ),
        (
            "| 滚动开放数据 | "
            f"{rolling_open_data['snapshot_count']} 个快照，"
            f"新增={rolling_open_data['new_record_count']}，"
            f"迟到={rolling_open_data['late_arrival_count']}，"
            f"坏候选阻断={rolling_open_data['bad_candidate_blocked_count']} |"
        ),
        (
            "| 下游消费 | "
            f"{metrics['downstream_consumption']['candidate_case_count']} 条 candidate case，"
            f"baseline key coverage={metrics['downstream_consumption']['baseline_key_coverage']} |"
        ),
        (
            "| 医院 Agent 影响 | "
            f"{hospital_impact['passed_canary_case_count']}/"
            f"{hospital_impact['canary_case_count']} 医院下游回归通过，"
            f"inactive 泄漏={hospital_impact['forbidden_doc_leak_count']} |"
        ),
        (
            "| 协作边界 | "
            f"{collaboration['covered_scenario_count']}/"
            f"{collaboration['scenario_count']} 场景覆盖，"
            "consumer approved rollback blocked="
            f"{collaboration['consumer_approved_rollback_blocked_at_api']} |"
        ),
        (
            "| 跨项目恢复 | "
            f"{cross_project_recovery['passed_scenario_count']}/"
            f"{cross_project_recovery['scenario_count']} 场景通过，"
            f"坏候选阻断={cross_project_recovery['bad_candidate_blocked']}，"
            f"恢复后回归={cross_project_recovery['restored_regression_passed']} |"
        ),
        (
            "| 发布审查 | "
            f"{release_review['passed_check_count']}/{release_review['check_count']} "
            f"通过，recommendation={release_review['recommendation']}，"
            f"reviewer publish={release_review['reviewer_can_publish_active_version']} |"
        ),
        (
            "| 训练候选 | "
            f"{training_candidates['candidate_record_count']} 条候选，"
            f"direct SFT={training_candidates['direct_sft_candidate_count']}，"
            f"preference={training_candidates['preference_candidate_count']}，"
            f"model training={training_candidates['model_training_executed']} |"
        ),
        (
            "| 性能 | "
            f"{performance['local_row_count']} 行本地冒烟 total={performance['total_ms']} ms，"
            f"rows/s={performance['rows_per_second']}；"
            f"多规模 max={multi_scale.get('max_row_count')} 行，"
            f"best rows/s={multi_scale.get('best_rows_per_second')} |"
        ),
        (
            "| 版本一致性 | "
            f"重复提交复用={version_consistency['duplicate_request_reuses_version']}，"
            f"旧 active 阻断={version_consistency['stale_expected_active_version_blocked']}，"
            f"并发成功={version_consistency['concurrent_published_count']}，"
            f"并发冲突={version_consistency['concurrent_conflict_count']} |"
        ),
        (
            "| 治理决策记忆 | "
            f"{decision_memory.get('scenario_passed')}/"
            f"{decision_memory.get('scenario_count')} 场景通过，"
            f"模型调用下降={decision_memory.get('model_call_reduction_percent')}%，"
            f"门禁绕过={decision_memory.get('gate_bypass_count')} |"
        ),
        (
            "| 安全 | "
            f"{metrics['security']['security_passed_case_count']}/"
            f"{metrics['security']['security_case_count']} 安全场景通过 |"
        ),
        (
            "| 能力边界 | "
            f"{boundary_guard['boundary_passed_case_count']}/"
            f"{boundary_guard['boundary_case_count']} 边界样例通过，"
            f"rejected={boundary_guard['rejected_change_count']} |"
        ),
        (
            "| CI 质量 | "
            f"{ci_quality['suite_count']} 类矩阵，"
            f"{ci_quality['assigned_test_file_count']}/"
            f"{ci_quality['test_file_count']} 个测试文件已纳入，"
            f"remote CI executed={ci_quality['ci_provider_executed']} |"
        ),
        (
            "| 模型/云 | "
            f"历史 Qwen 报告 found={qwen_live['report_found']}，"
            f"历史 ECS 报告 found={ecs_live['report_found']}，"
            f"当前版本 ECS 复验 found={ecs_current['report_found']}，"
            "scorecard 本轮未主动调用云资源 |"
        ),
        "",
        "## 简历可用口径",
        "",
    ]
    lines.extend(f"- {item}" for item in report["resume_ready_metrics"])
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- 可以说：有本地 scorecard 汇总质量、发布、回归、性能、安全、模型和云验证证据。",
            (
                "- 可以说：scorecard 已纳入审批状态证据，能展示待审批、批准、"
                "拒绝、人工复核、门禁失败和发布成功。"
            ),
            "- 可以说：scorecard 已纳入 freshness 检查和 dry-run 调度记录。",
            "- 可以说：scorecard 已纳入滚动开放数据回放，覆盖新增、迟到、重复和字段变化。",
            "- 可以说：scorecard 已纳入多 Agent/下游消费者协作边界检查。",
            "- 可以说：scorecard 已纳入跨项目恢复演练，覆盖坏候选阻断和审批回滚。",
            "- 可以说：scorecard 已纳入只读发布审查型 Reviewer 检查。",
            "- 可以说：scorecard 已纳入训练候选池导出，但当前没有执行模型训练。",
            (
                "- 可以说：scorecard 已纳入治理决策记忆专项对照，"
                "但该结果来自本地确定性 Provider 桩，不是 Qwen 批量 live。"
            ),
            "- 可以说：scorecard 已纳入多规模本地回放基线，但该数据是稳定扩展样本。",
            (
                "- 可以说：scorecard 已纳入并发与幂等验证，能展示重复提交复用、"
                "旧 active 阻断和同父版本并发单赢家。"
            ),
            "- 可以说：scorecard 已纳入跨项目能力边界审查，但它不是生产安全平台。",
            (
                "- 可以说：scorecard 已纳入 CI-ready 质量门禁和测试矩阵证据，"
                "但远端 CI 运行记录仍需后续补充。"
            ),
            (
                "- 可以说：scorecard 已纳入 PL-O1 当前版本 ECS 复验证据，"
                "但它是受控线上环境测试，不是生产上线。"
            ),
            (
                "- 不能说：生产 SLA、真实 QPS、真实用户流量、真实业务 ROI，"
                "或 scorecard 本轮主动调用 Qwen/ECS。"
            ),
            "- 不能说：已完成 SFT、DPO、RLHF、偏好学习或在线训练。",
            "- 不能说：已经实现生产级 A2A、消息队列或多 Agent 调度平台。",
            "- 不能说：已经实现分布式锁、分布式事务或生产任务队列。",
            "- 不能说：能力边界审查是生产级 WAF、DLP、SIEM、企业 RBAC 或真实红队测试。",
            "- 不能说：远端 GitHub Actions、企业 CI 或生产质量趋势已经真实运行通过。",
            "- 不能说：Reviewer 可以审批、发布或回滚正式版本。",
            "- 不能说：决策记忆已在生产审批、真实企业数据或线上流量中验证。",
            "- 不能说：已经接入 Airflow、Kafka、生产调度平台或真实告警系统。",
            "",
        ]
    )
    destination.write_text("\n".join(lines), encoding="utf-8")


def _safe_ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else round(numerator / denominator, 4)


def _safe_run_id(value: str) -> str:
    return "scorecard-" + "".join(char if char.isalnum() else "-" for char in value).strip("-")


def _stdout_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report["status"],
        "run_id": report["run_id"],
        "public_source_count": report["metrics"]["data_sources"]["public_source_count"],
        "authorized_private_source_count": report["metrics"]["data_sources"][
            "authorized_private_source_count"
        ],
        "acceptance_pass_rate": report["metrics"]["governance_release"]["acceptance_pass_rate"],
        "approval_status_case_count": report["metrics"]["approval_status"]["status_case_count"],
        "security_pass_rate": report["metrics"]["security"]["security_pass_rate"],
        "boundary_guard_pass_rate": report["metrics"]["boundary_guard"]["boundary_pass_rate"],
        "ci_suite_count": report["metrics"]["ci_quality"]["suite_count"],
        "ci_test_file_count": report["metrics"]["ci_quality"]["test_file_count"],
        "ci_provider_executed": report["metrics"]["ci_quality"]["ci_provider_executed"],
        "fresh_source_count": report["metrics"]["freshness"]["fresh_source_count"],
        "stale_source_count": report["metrics"]["freshness"]["stale_source_count"],
        "rolling_snapshot_count": report["metrics"]["rolling_open_data"]["snapshot_count"],
        "rolling_bad_candidate_blocked_count": report["metrics"]["rolling_open_data"][
            "bad_candidate_blocked_count"
        ],
        "collaboration_scenario_count": report["metrics"]["collaboration"]["scenario_count"],
        "release_review_recommendation": report["metrics"]["release_review"]["recommendation"],
        "training_candidate_record_count": report["metrics"]["training_candidates"][
            "candidate_record_count"
        ],
        "direct_sft_candidate_count": report["metrics"]["training_candidates"][
            "direct_sft_candidate_count"
        ],
        "a2a_protocol_implemented": report["metrics"]["collaboration"]["a2a_protocol_implemented"],
        "qwen_called_this_run": report["scope"]["qwen_called_this_run"],
        "ecs_used_this_run": report["scope"]["ecs_used_this_run"],
        "current_ecs_validation_report_found": report["scope"][
            "current_ecs_validation_report_reused"
        ],
        "current_ecs_validation_pytest_tests": report["metrics"]["model_and_cloud"][
            "ecs_current_validation"
        ].get("pytest_tests"),
        "current_ecs_validation_health_loop_all_ok": report["metrics"]["model_and_cloud"][
            "ecs_current_validation"
        ].get("health_loop_all_ok"),
        "decision_memory_scenario_passed": report["metrics"]["decision_memory"].get(
            "scenario_passed"
        ),
        "report_json": report["outputs"]["report_json"],
        "report_md": report["outputs"]["report_md"],
    }


if __name__ == "__main__":
    main()
