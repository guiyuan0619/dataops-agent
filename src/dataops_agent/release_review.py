from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dataops_agent.acceptance import run_acceptance
from dataops_agent.collaboration import run_collaboration_report
from dataops_agent.performance import run_performance_report
from dataops_agent.security_review import run_security_review

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "dataops_release_review.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "dataops_release_review.md"
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT / "reports" / "release_review_runtime"

REVIEWER_POLICY = {
    "agent": "release_risk_reviewer",
    "role": "readonly_release_reviewer",
    "goal": "独立复核候选版本是否可以进入人工审批，而不是直接发布或回滚。",
    "allowed_operations": [
        "read_acceptance_report",
        "read_security_review",
        "read_performance_smoke",
        "read_collaboration_contract",
        "recommend_human_approval",
        "recommend_hold",
    ],
    "denied_operations": [
        "approve_publish",
        "publish_active_version",
        "rollback_active_version",
        "execute_shell",
        "write_candidate_or_active_version",
    ],
    "permissions": {
        "can_approve_publish": False,
        "can_publish_active_version": False,
        "can_rollback_active_version": False,
        "can_execute_shell": False,
        "can_write_dataset": False,
    },
}

GOVERNANCE_STATE_GRAPH = [
    {
        "node": "candidate_intake",
        "owner": "dataops_agent",
        "gate": "source manifest, schema, checksum, and candidate boundary exist",
    },
    {
        "node": "quality_and_repair_preview",
        "owner": "dataops_agent",
        "gate": "quality report and deterministic repair preview are generated",
    },
    {
        "node": "release_risk_review",
        "owner": "release_risk_reviewer",
        "gate": "acceptance, security, performance, collaboration, and permission checks pass",
    },
    {
        "node": "human_approval_checkpoint",
        "owner": "human_approver",
        "gate": "human approval is still required before formal publish",
    },
    {
        "node": "version_publish",
        "owner": "dataops_agent",
        "gate": "candidate checksum is rechecked before active version switch",
    },
    {
        "node": "consumer_regression_and_audit",
        "owner": "consumer_agent",
        "gate": "downstream regression, trace, and thread audit remain readable",
    },
]


def run_release_review_report(
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

    acceptance = run_acceptance(
        report_json=runtime_dir / "acceptance.json",
        report_md=runtime_dir / "acceptance.md",
        runtime_root=runtime_dir / "acceptance_runtime",
        run_id=f"{run_id}-acceptance",
    )
    security = run_security_review(
        report_json=runtime_dir / "security_review.json",
        report_md=runtime_dir / "security_review.md",
        runtime_root=runtime_dir / "security_review_runtime",
        run_id=f"{run_id}-security",
    )
    performance = run_performance_report(
        report_json=runtime_dir / "performance.json",
        report_md=runtime_dir / "performance.md",
        runtime_root=runtime_dir / "performance_runtime",
        run_id=f"{run_id}-performance",
    )
    collaboration = run_collaboration_report(
        report_json=runtime_dir / "collaboration.json",
        report_md=runtime_dir / "collaboration.md",
        run_id=f"{run_id}-collaboration",
    )

    checks = _review_checks(acceptance, security, performance, collaboration)
    blocking_checks = [check for check in checks if check["status"] == "hold"]
    report = {
        "status": "ok" if not blocking_checks else "failed",
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
            "reviewer_can_write": False,
            "reviewer_can_approve": False,
        },
        "reviewer": REVIEWER_POLICY,
        "governance_state_graph": GOVERNANCE_STATE_GRAPH,
        "component_reports": {
            "acceptance": acceptance["outputs"],
            "security": security["outputs"],
            "performance": performance["outputs"],
            "collaboration": collaboration["outputs"],
        },
        "checks": checks,
        "metrics": {
            "check_count": len(checks),
            "passed_check_count": sum(1 for check in checks if check["status"] == "pass"),
            "blocking_check_count": len(blocking_checks),
            "recommendation": "hold_for_fix"
            if blocking_checks
            else "ready_for_human_approval",
            "reviewer_can_approve_publish": REVIEWER_POLICY["permissions"][
                "can_approve_publish"
            ],
            "reviewer_can_publish_active_version": REVIEWER_POLICY["permissions"][
                "can_publish_active_version"
            ],
            "reviewer_can_rollback_active_version": REVIEWER_POLICY["permissions"][
                "can_rollback_active_version"
            ],
            "reviewer_can_execute_shell": REVIEWER_POLICY["permissions"]["can_execute_shell"],
            "state_graph_node_count": len(GOVERNANCE_STATE_GRAPH),
        },
        "resume_ready_metrics": [
            "新增只读 Release Reviewer，用独立审查结论决定候选版本是否可进入人工审批。",
            "Reviewer 复核验收门禁、安全对抗、性能冒烟、消费者协作和自身权限边界。",
            "Reviewer 不能审批、发布、回滚、写数据或执行 Shell，正式写入仍留在 DataOps 审批链路。",
            (
                "DataOps 主链路可解释为治理状态图：候选接入、质量预览、"
                "风险审查、人工审批、版本发布、下游回归。"
            ),
        ],
        "boundary": {
            "can_say": [
                (
                    "DataOps 已有发布审查型多 Agent 闭环，Reviewer 独立检查发布风险"
                    "并给出进入人工审批的建议。"
                ),
                "Reviewer 是 evaluator/guardrail 角色，不是全局 Planner，也不直接执行写操作。",
                "治理状态图把数据发布拆成候选、预览、审查、审批、发布、回归和审计节点。",
            ],
            "cannot_say": [
                "不能说 Reviewer 可以审批、发布或回滚正式版本。",
                "不能说已经实现生产级多 Agent 调度平台、A2A、消息队列或多租户权限系统。",
                "不能说本轮启用了 Qwen、ECS 或真实业务流量。",
            ],
        },
    }
    _write_json(report_json, report)
    _write_markdown(report_md, report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run DataOps release-risk reviewer agent report."
    )
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    report = run_release_review_report(
        report_json=args.report_json,
        report_md=args.report_md,
        runtime_root=args.runtime_root,
        run_id=args.run_id,
    )
    print(json.dumps(_stdout_summary(report), ensure_ascii=False, indent=2))
    if report["status"] != "ok":
        raise SystemExit(1)


def _review_checks(
    acceptance: dict[str, Any],
    security: dict[str, Any],
    performance: dict[str, Any],
    collaboration: dict[str, Any],
) -> list[dict[str, Any]]:
    acceptance_summary = acceptance["summary"]
    security_summary = security["summary"]
    performance_gates = performance["gates"]
    collaboration_metrics = collaboration["metrics"]
    return [
        _check(
            "acceptance_gates_pass",
            acceptance["status"] == "ok"
            and acceptance_summary["failed_case_count"] == 0,
            "候选进入审批前，7 类失败门禁必须全部通过。",
            {
                "passed": acceptance_summary["passed_case_count"],
                "total": acceptance_summary["case_count"],
            },
        ),
        _check(
            "security_review_pass",
            security["status"] == "ok" and security_summary["failed_case_count"] == 0,
            "候选进入审批前，越权、注入、路径、来源和回滚边界必须全部通过。",
            {
                "passed": security_summary["passed_case_count"],
                "total": security_summary["case_count"],
            },
        ),
        _check(
            "performance_smoke_pass",
            performance["status"] == "ok" and all(performance_gates.values()),
            "候选进入审批前，本地 500 行服务请求质量检测、发布和回归链路必须跑通。",
            {
                "row_count": performance["dataset_scale"]["service_requests_row_count"],
                "total_ms": performance["timings_ms"]["total"],
            },
        ),
        _check(
            "consumer_collaboration_boundary_pass",
            collaboration["status"] == "ok"
            and collaboration_metrics["downstream_can_approve_count"] == 0
            and collaboration_metrics["consumer_approved_rollback_blocked_at_api"],
            "候选进入审批前，下游消费者不能拥有审批、发布或回滚正式版本的权限。",
            {
                "scenario_count": collaboration_metrics["scenario_count"],
                "downstream_can_approve_count": collaboration_metrics[
                    "downstream_can_approve_count"
                ],
            },
        ),
        _check(
            "reviewer_permission_boundary_pass",
            not any(REVIEWER_POLICY["permissions"].values()),
            "Reviewer 只能读证据和给建议，不能审批、发布、回滚、写数据或执行 Shell。",
            REVIEWER_POLICY["permissions"],
        ),
    ]


def _check(
    check_id: str,
    passed: bool,
    rationale: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": "pass" if passed else "hold",
        "rationale": rationale,
        "evidence": evidence,
    }


def _write_json(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_markdown(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    metrics = report["metrics"]
    lines = [
        "# DataOps Agent 发布审查型多 Agent 报告",
        "",
        f"- 运行 ID：`{report['run_id']}`",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 结果：`{report['status']}`",
        f"- 审查建议：`{metrics['recommendation']}`",
        "- 范围：本地只读发布风险审查；本轮未启用 ECS，未调用 Qwen，非生产多 Agent 调度。",
        "",
        "## 核心指标",
        "",
        "| 维度 | 指标 |",
        "|---|---|",
        (
            "| 审查检查 | "
            f"{metrics['passed_check_count']}/{metrics['check_count']} 通过，"
            f"blocking={metrics['blocking_check_count']} |"
        ),
        (
            "| Reviewer 权限 | "
            f"approve={metrics['reviewer_can_approve_publish']}，"
            f"publish={metrics['reviewer_can_publish_active_version']}，"
            f"rollback={metrics['reviewer_can_rollback_active_version']}，"
            f"shell={metrics['reviewer_can_execute_shell']} |"
        ),
        f"| 治理状态图 | {metrics['state_graph_node_count']} 个节点 |",
        "",
        "## Reviewer 角色",
        "",
        f"- 目标：{report['reviewer']['goal']}",
        f"- 可以做：{'、'.join(report['reviewer']['allowed_operations'])}",
        f"- 不能做：{'、'.join(report['reviewer']['denied_operations'])}",
        "",
        "## 审查项",
        "",
        "| 审查项 | 状态 | 说明 |",
        "|---|---|---|",
    ]
    for check in report["checks"]:
        lines.append(f"| {check['id']} | {check['status']} | {check['rationale']} |")
    lines.extend(
        [
            "",
            "## 治理状态图",
            "",
            "| 节点 | 负责人 | 门禁 |",
            "|---|---|---|",
        ]
    )
    for node in report["governance_state_graph"]:
        lines.append(f"| {node['node']} | {node['owner']} | {node['gate']} |")
    lines.extend(
        [
            "",
            "## 简历可用口径",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in report["resume_ready_metrics"])
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- 可以说：DataOps 已有只读发布审查型 Reviewer，用已有证据判断候选是否能进入人工审批。",
            "- 可以说：Reviewer 是 evaluator/guardrail 角色，不是全局 Planner。",
            "- 不能说：Reviewer 可以审批、发布或回滚正式版本。",
            "- 不能说：已经实现生产级 A2A、消息队列、多 Agent 调度平台或真实流量协作。",
            "",
        ]
    )
    destination.write_text("\n".join(lines), encoding="utf-8")


def _safe_run_id(value: str) -> str:
    return "release-review-" + "".join(
        char if char.isalnum() else "-" for char in value
    ).strip("-")


def _stdout_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report["status"],
        "run_id": report["run_id"],
        "recommendation": report["metrics"]["recommendation"],
        "check_count": report["metrics"]["check_count"],
        "passed_check_count": report["metrics"]["passed_check_count"],
        "blocking_check_count": report["metrics"]["blocking_check_count"],
        "reviewer_can_approve_publish": report["metrics"]["reviewer_can_approve_publish"],
        "reviewer_can_publish_active_version": report["metrics"][
            "reviewer_can_publish_active_version"
        ],
        "report_json": report["outputs"]["report_json"],
        "report_md": report["outputs"]["report_md"],
    }


if __name__ == "__main__":
    main()
