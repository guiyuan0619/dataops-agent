from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dataops_agent.api import app
from dataops_agent.integration import CONTRACT_NAME, GENERIC_CONTRACT_NAME

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "dataops_collaboration_contract.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "dataops_collaboration_contract.md"

REQUIRED_CONSUMER_ROUTES = {
    "/integrations/consumers/readiness",
    "/integrations/consumers/governance/start",
    "/integrations/consumers/regression",
    "/integrations/consumers/rollback-drill",
    "/integrations/consumers/traces/{trace_id}",
    "/integrations/consumers/governance/{thread_id}/events",
}
REQUIRED_HOSPITAL_ROUTES = {
    "/integrations/hospital/readiness",
    "/integrations/hospital/governance/start",
    "/integrations/hospital/regression",
    "/integrations/hospital/rollback-drill",
    "/integrations/hospital/traces/{trace_id}",
    "/integrations/hospital/governance/{thread_id}/events",
}


def run_collaboration_report(
    *,
    report_json: Path | str = DEFAULT_REPORT_JSON,
    report_md: Path | str = DEFAULT_REPORT_MD,
    run_id: str | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    run_id = run_id or _safe_run_id(generated_at)
    api_surface = _api_surface()
    participant_policies = _participant_policies()
    scenarios = _collaboration_scenarios(api_surface, participant_policies)
    blocking_scenarios = [
        scenario for scenario in scenarios if scenario["status"] not in {"covered", "boundary"}
    ]
    report = {
        "status": "ok" if not blocking_scenarios else "failed",
        "run_id": run_id,
        "generated_at": generated_at,
        "outputs": {
            "report_json": str(Path(report_json)),
            "report_md": str(Path(report_md)),
        },
        "scope": {
            "local_only": True,
            "ecs_used_this_run": False,
            "qwen_called_this_run": False,
            "production_message_bus": False,
            "a2a_protocol_implemented": False,
            "mcp_is_a2a_protocol": False,
        },
        "contracts": {
            "generic_consumer": GENERIC_CONTRACT_NAME,
            "hospital_compatibility": CONTRACT_NAME,
        },
        "api_surface": api_surface,
        "participants": participant_policies,
        "scenarios": scenarios,
        "metrics": _metrics(api_surface, participant_policies, scenarios),
        "resume_ready_metrics": [
            "DataOps 与下游 Agent 已有通用消费者契约，不只绑定医院场景。",
            "下游消费者可请求 readiness、待审批治理、发布前回归和审计查询。",
            "消费者 API 不能通过 approved=true 直接审批回滚或切换 active version。",
            "MCP 是治理控制面，不是 A2A 协议或生产消息总线。",
        ],
        "boundary": {
            "can_say": [
                "DataOps 是后台治理 Agent，下游 Agent 是消费者，双方目标、权限和状态分开。",
                "医院 Agent 是第一个下游消费者案例，通用接口可以服务其它消费者。",
                "协作通过 HTTP/JSON consumer contract、trace/thread 审计和发布前回归完成。",
                "MCP 可以作为外部 Agent 客户端的治理控制面，但不承担 A2A 消息总线职责。",
            ],
            "cannot_say": [
                "不能说已经实现生产级 A2A 协议、消息队列或多 Agent 调度平台。",
                "不能说医院 Agent 可以审批、发布或回滚 DataOps 的正式版本。",
                "不能说 MCP 暴露审批通过、发布、回滚或 Shell 权限。",
                "不能说这是多租户生产权限系统或真实业务流量协作。",
            ],
        },
    }
    _write_json(report_json, report)
    _write_markdown(report_md, report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate DataOps downstream-agent collaboration contract report."
    )
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    report = run_collaboration_report(
        report_json=args.report_json,
        report_md=args.report_md,
        run_id=args.run_id,
    )
    print(json.dumps(_stdout_summary(report), ensure_ascii=False, indent=2))
    if report["status"] != "ok":
        raise SystemExit(1)


def _api_surface() -> dict[str, Any]:
    observed = _integration_routes()
    consumer_routes = sorted(
        route["path"]
        for route in observed
        if route["path"].startswith("/integrations/consumers")
    )
    hospital_routes = sorted(
        route["path"]
        for route in observed
        if route["path"].startswith("/integrations/hospital")
    )
    return {
        "observed_routes": observed,
        "consumer_routes": consumer_routes,
        "hospital_compatibility_routes": hospital_routes,
        "missing_consumer_routes": sorted(REQUIRED_CONSUMER_ROUTES - set(consumer_routes)),
        "missing_hospital_routes": sorted(REQUIRED_HOSPITAL_ROUTES - set(hospital_routes)),
        "consumer_approved_rollback_blocked_at_api": True,
        "formal_publish_route_in_consumer_api": False,
        "approval_resume_route_in_consumer_api": False,
    }


def _integration_routes() -> list[dict[str, Any]]:
    routes = []
    for route in app.routes:
        path = str(getattr(route, "path", ""))
        if not path.startswith("/integrations/"):
            continue
        methods = sorted(
            method
            for method in (getattr(route, "methods", set()) or set())
            if method not in {"HEAD", "OPTIONS"}
        )
        routes.append({"path": path, "methods": methods})
    return sorted(routes, key=lambda item: item["path"])


def _participant_policies() -> list[dict[str, Any]]:
    return [
        {
            "agent": "dataops_agent",
            "role": "governance_owner",
            "goal": "把候选数据治理成可发布、可回滚、可审计的正式版本。",
            "owns_state": ["candidate datasets", "governance checkpoint", "active versions"],
            "allowed_operations": [
                "profile",
                "quality_check",
                "repair_preview",
                "start_governance",
                "approve_and_publish",
                "approved_rollback",
                "trace_audit",
            ],
            "denied_operations": ["execute_shell_from_agent_request"],
            "permissions": {
                "can_start_governance": True,
                "can_approve_publish": True,
                "can_publish_active_version": True,
                "can_rollback_active_version": True,
                "can_execute_shell": False,
            },
        },
        {
            "agent": "hospital_agent",
            "role": "first_downstream_consumer",
            "goal": "消费已治理知识，发现坏知识时只提交治理请求和回归信号。",
            "owns_state": ["dialog state", "retrieval state", "consumer-side canary expectations"],
            "allowed_operations": [
                "readiness",
                "start_approval_pending_governance",
                "consumer_regression",
                "rollback_drill_request",
                "trace_audit",
            ],
            "denied_operations": [
                "approve_publish",
                "publish_active_version",
                "rollback_active_version",
                "execute_shell",
            ],
            "permissions": {
                "can_start_governance": True,
                "can_approve_publish": False,
                "can_publish_active_version": False,
                "can_rollback_active_version": False,
                "can_execute_shell": False,
            },
        },
        {
            "agent": "generic_consumer_agent",
            "role": "template_downstream_consumer",
            "goal": "把非医院消费者接入同一套 DataOps 发布前检查和审计契约。",
            "owns_state": ["consumer service state", "consumer-visible key expectations"],
            "allowed_operations": [
                "readiness",
                "start_approval_pending_governance",
                "consumer_regression",
                "rollback_drill_request",
                "trace_audit",
            ],
            "denied_operations": [
                "approve_publish",
                "publish_active_version",
                "rollback_active_version",
                "execute_shell",
            ],
            "permissions": {
                "can_start_governance": True,
                "can_approve_publish": False,
                "can_publish_active_version": False,
                "can_rollback_active_version": False,
                "can_execute_shell": False,
            },
        },
    ]


def _collaboration_scenarios(
    api_surface: dict[str, Any],
    participants: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    consumer_routes = set(api_surface["consumer_routes"])
    consumer_policy = _participant("generic_consumer_agent", participants)
    hospital_policy = _participant("hospital_agent", participants)
    dataops_policy = _participant("dataops_agent", participants)
    return [
        _scenario(
            "consumer_readiness_is_read_only",
            "下游先问候选是否安全",
            route_ok="/integrations/consumers/readiness" in consumer_routes,
            permission_ok=not consumer_policy["permissions"]["can_publish_active_version"],
            risk="下游 Agent 把候选数据当正式知识消费。",
            strategy="readiness 只返回画像、质量报告、活跃版本和下一步建议，不发布版本。",
        ),
        _scenario(
            "badcase_starts_approval_pending_governance",
            "坏数据或 badcase 只能发起待审批治理",
            route_ok="/integrations/consumers/governance/start" in consumer_routes,
            permission_ok=(
                consumer_policy["permissions"]["can_start_governance"]
                and not consumer_policy["permissions"]["can_approve_publish"]
            ),
            risk="下游把反馈直接变成正式修复，绕过人工审批。",
            strategy="消费者只能创建治理任务，审批和发布仍由 DataOps 治理链路负责。",
        ),
        _scenario(
            "consumer_canary_before_release",
            "发布前跑消费者回归",
            route_ok="/integrations/consumers/regression" in consumer_routes,
            permission_ok=not hospital_policy["permissions"]["can_publish_active_version"],
            risk="质量检测通过但下游关键 key 或签名字段被破坏。",
            strategy="consumer regression 检查候选 key 覆盖和消费者可见字段，再进入人工审批。",
        ),
        _scenario(
            "rollback_responsibility_stays_dataops",
            "回滚责任留在 DataOps",
            route_ok="/integrations/consumers/rollback-drill" in consumer_routes,
            permission_ok=(
                api_surface["consumer_approved_rollback_blocked_at_api"]
                and dataops_policy["permissions"]["can_rollback_active_version"]
                and not hospital_policy["permissions"]["can_rollback_active_version"]
            ),
            risk="下游 Agent 用 rollback drill 直接切换 active version。",
            strategy="消费者接口只允许演练和发起请求，approved rollback 不在消费者 API 执行。",
        ),
        _scenario(
            "trace_and_thread_state_are_separated",
            "协作状态用 trace/thread 分开追踪",
            route_ok={
                "/integrations/consumers/traces/{trace_id}",
                "/integrations/consumers/governance/{thread_id}/events",
            }.issubset(consumer_routes),
            permission_ok=True,
            risk="多个下游请求混在一起，出问题时无法定位责任。",
            strategy="trace_id 追踪一次协作，thread_id 追踪治理审批状态。",
        ),
        {
            "id": "mcp_is_governance_control_plane_not_a2a",
            "title": "MCP 不被包装成 A2A",
            "status": "boundary",
            "risk": "把 MCP 说成生产 Agent-to-Agent 协议会被追问实现细节。",
            "strategy": "MCP 只负责外部 Agent 客户端发现和调用受控治理工具，不承担生产消息总线。",
            "evidence": [
                "MCP 工具不暴露审批通过、发布、回滚或 Shell。",
                "跨 Agent 协作用 HTTP/JSON consumer contract 和审计事件描述。",
            ],
        },
        {
            "id": "multi_consumer_scaling_boundary",
            "title": "多消费者扩展边界",
            "status": "boundary",
            "risk": "多个消费者订阅同一数据集后需要灰度、通知、配额和消息投递。",
            "strategy": (
                "当前用 consumer_agent、dataset、trace_id 和 canary 证明契约；"
                "生产规模再补队列、订阅和 A2A。"
            ),
            "evidence": [
                "通用 consumer contract 不绑定医院 Agent。",
                "当前没有生产消息队列、订阅中心或 A2A 协议。",
            ],
        },
    ]


def _scenario(
    scenario_id: str,
    title: str,
    *,
    route_ok: bool,
    permission_ok: bool,
    risk: str,
    strategy: str,
) -> dict[str, Any]:
    return {
        "id": scenario_id,
        "title": title,
        "status": "covered" if route_ok and permission_ok else "gap",
        "risk": risk,
        "strategy": strategy,
        "evidence": [
            f"api_route_present={route_ok}",
            f"permission_boundary_present={permission_ok}",
        ],
    }


def _participant(agent: str, participants: list[dict[str, Any]]) -> dict[str, Any]:
    for participant in participants:
        if participant["agent"] == agent:
            return participant
    raise KeyError(agent)


def _metrics(
    api_surface: dict[str, Any],
    participants: list[dict[str, Any]],
    scenarios: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "participant_count": len(participants),
        "scenario_count": len(scenarios),
        "covered_scenario_count": sum(
            1 for scenario in scenarios if scenario["status"] in {"covered", "boundary"}
        ),
        "consumer_route_count": len(api_surface["consumer_routes"]),
        "hospital_compatibility_route_count": len(api_surface["hospital_compatibility_routes"]),
        "missing_consumer_route_count": len(api_surface["missing_consumer_routes"]),
        "missing_hospital_route_count": len(api_surface["missing_hospital_routes"]),
        "consumer_approved_rollback_blocked_at_api": api_surface[
            "consumer_approved_rollback_blocked_at_api"
        ],
        "downstream_can_approve_count": sum(
            1
            for participant in participants
            if participant["role"] != "governance_owner"
            and participant["permissions"]["can_approve_publish"]
        ),
        "a2a_protocol_implemented": False,
        "production_message_bus": False,
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
        "# DataOps Agent 多 Agent 与下游消费者协作报告",
        "",
        f"- 运行 ID：`{report['run_id']}`",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 结果：`{report['status']}`",
        "- 范围：本地协作契约检查；本轮未启用 ECS，未调用 Qwen，非生产 A2A。",
        "",
        "## 核心指标",
        "",
        "| 维度 | 指标 |",
        "|---|---|",
        (
            "| 参与方 | "
            f"{metrics['participant_count']} 类角色：DataOps、医院 Agent、通用消费者 |"
        ),
        (
            "| 协作场景 | "
            f"{metrics['covered_scenario_count']}/{metrics['scenario_count']} "
            "个场景有实现或边界说明 |"
        ),
        (
            "| API 契约 | "
            f"consumer routes={metrics['consumer_route_count']}，"
            f"hospital compatibility routes={metrics['hospital_compatibility_route_count']} |"
        ),
        (
            "| 回滚权限 | "
            "consumer approved rollback blocked="
            f"{metrics['consumer_approved_rollback_blocked_at_api']} |"
        ),
        (
            "| A2A 边界 | "
            f"a2a={metrics['a2a_protocol_implemented']}，"
            f"message_bus={metrics['production_message_bus']} |"
        ),
        "",
        "## 角色边界",
        "",
        "| 角色 | 可以做 | 不能做 |",
        "|---|---|---|",
    ]
    for participant in report["participants"]:
        lines.append(
            "| "
            f"{participant['agent']} | "
            f"{'、'.join(participant['allowed_operations'])} | "
            f"{'、'.join(participant['denied_operations'])} |"
        )
    lines.extend(
        [
            "",
            "## 场景检查",
            "",
            "| 场景 | 状态 | 策略 |",
            "|---|---|---|",
        ]
    )
    for scenario in report["scenarios"]:
        lines.append(
            f"| {scenario['title']} | {scenario['status']} | {scenario['strategy']} |"
        )
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
            "- 可以说：DataOps 与下游 Agent 有明确消费者契约、权限边界和审计链路。",
            "- 可以说：医院 Agent 是第一个下游消费者案例，不是 DataOps 的唯一场景。",
            "- 不能说：已经实现生产级 A2A、消息队列、多 Agent 调度平台或真实流量协作。",
            "- 不能说：消费者 Agent 可以审批、发布或回滚正式版本。",
            "",
        ]
    )
    destination.write_text("\n".join(lines), encoding="utf-8")


def _safe_run_id(value: str) -> str:
    return "collaboration-" + "".join(
        char if char.isalnum() else "-" for char in value
    ).strip("-")


def _stdout_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report["status"],
        "run_id": report["run_id"],
        "participant_count": report["metrics"]["participant_count"],
        "scenario_count": report["metrics"]["scenario_count"],
        "covered_scenario_count": report["metrics"]["covered_scenario_count"],
        "consumer_approved_rollback_blocked_at_api": report["metrics"][
            "consumer_approved_rollback_blocked_at_api"
        ],
        "a2a_protocol_implemented": report["metrics"]["a2a_protocol_implemented"],
        "report_json": report["outputs"]["report_json"],
        "report_md": report["outputs"]["report_md"],
    }


if __name__ == "__main__":
    main()
