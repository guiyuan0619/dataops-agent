from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "dataops_scenario_playbook.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "dataops_scenario_playbook.md"

CORE_MVP_NODES = [
    "authorized_source_or_public_snapshot",
    "schema_contract",
    "quality_and_freshness_gate",
    "repair_preview",
    "human_approval",
    "version_publish",
    "consumer_regression",
    "trace_and_rollback",
]

SCENARIOS = [
    {
        "scenario_id": "enterprise_data_analytics",
        "title": "企业数据分析 Agent",
        "target_user": "业务分析师、运营负责人、AI 应用工程师",
        "input_sources": [
            "授权数据库导出",
            "SaaS 报表导出",
            "指标口径文档",
            "公开行业数据快照",
        ],
        "downstream_consumer": "数据分析问答 Agent 或 BI 报表助手",
        "north_star_metric": "已发布数据集被问答或报表消费时的口径错误率下降",
        "mvp_nodes": CORE_MVP_NODES,
        "top_risks": [
            "指标口径漂移导致同一个 KPI 前后含义不一致",
            "授权范围不清导致私有字段或凭证进入候选数据",
            "数据过期但仍被分析 Agent 当作最新事实",
        ],
        "dataops_controls": [
            "数据合同固定主键、必填字段和口径说明",
            "私有/授权来源 adapter 记录授权、脱敏和凭证不落盘边界",
            "freshness/checksum 检查和 dry-run 刷新避免过期数据自动覆盖",
            "consumer regression 检查下游依赖 key 和关键字段是否缺失",
        ],
        "not_in_mvp": ["生产数仓血缘平台", "实时流式指标", "多租户权限中心"],
    },
    {
        "scenario_id": "customer_service_knowledge",
        "title": "智能客服知识库",
        "target_user": "客服运营、知识库维护人员、RAG 应用工程师",
        "input_sources": [
            "FAQ 文档",
            "产品政策更新",
            "客服工单 badcase",
            "公告或服务条款快照",
        ],
        "downstream_consumer": "客服 RAG Agent 或服务助手",
        "north_star_metric": "知识更新后客服答案引用过期来源的比例下降",
        "mvp_nodes": CORE_MVP_NODES,
        "top_risks": [
            "旧政策没有下线导致客服继续引用过期答案",
            "用户或网页内容里的 prompt 注入被当成系统指令",
            "未经审核的知识直接进入 RAG 索引",
        ],
        "dataops_controls": [
            "RAG-ready JSONL 和 manifest 只导出通过质量门禁的数据版本",
            "安全对抗审查覆盖注入文本、未知工具和路径越界",
            "人工审批与版本回滚保证错误知识可撤回",
            "下游 canary 验证关键知识是否仍可被消费者读取",
        ],
        "not_in_mvp": ["完整向量库蓝绿切换", "真实客服在线 ROI", "生产 DLP 平台"],
    },
    {
        "scenario_id": "product_catalog_governance",
        "title": "商品目录与搜索治理",
        "target_user": "电商运营、商品数据维护、搜索/推荐应用工程师",
        "input_sources": [
            "商品 CSV",
            "供应商报价表",
            "库存或上下架导出",
            "商品类目规则",
        ],
        "downstream_consumer": "商品搜索 Agent、导购助手或推荐前置服务",
        "north_star_metric": "发布后 SKU 可见性、价格/状态一致性和搜索回归通过率",
        "mvp_nodes": CORE_MVP_NODES,
        "top_risks": [
            "重复 SKU 或缺主键导致商品覆盖错误",
            "价格、库存或上下架状态过期",
            "类目枚举不一致导致搜索或推荐召回异常",
        ],
        "dataops_controls": [
            "schema contract 和唯一性门禁固定 SKU 主键与枚举",
            "freshness policy 区分低频类目和高频库存状态",
            "发布前 consumer regression 检查关键 SKU 和类目覆盖",
            "版本回滚避免错误目录长期影响搜索入口",
        ],
        "not_in_mvp": ["实时库存交易系统", "推荐模型训练平台", "商品主数据中心"],
    },
    {
        "scenario_id": "content_ops_governance",
        "title": "内容生产与知识发布",
        "target_user": "内容运营、审核人员、知识发布负责人",
        "input_sources": [
            "内容选题表",
            "审核规则表",
            "已发布内容清单",
            "生成式内容候选稿",
        ],
        "downstream_consumer": "内容生产 Agent、审核助手或知识发布门户",
        "north_star_metric": "未经审核内容进入正式知识库的比例下降",
        "mvp_nodes": CORE_MVP_NODES,
        "top_risks": [
            "生成式内容未经审核直接发布",
            "审核规则版本变化后旧内容仍被引用",
            "缺少 trace 导致问题内容无法追溯来源和审批记录",
        ],
        "dataops_controls": [
            "候选区和人工审批阻断自动发布",
            "版本发布和 trace/thread 审计记录来源、审批和回滚过程",
            "安全审查把提示词注入、工具越权和本地文件读取挡在外面",
            "scorecard 汇总质量、发布、安全和下游消费证据",
        ],
        "not_in_mvp": ["版权合规自动判定", "全自动内容审核", "生产级审核工作台"],
    },
]


def run_scenario_playbook(
    *,
    report_json: Path | str = DEFAULT_REPORT_JSON,
    report_md: Path | str = DEFAULT_REPORT_MD,
    run_id: str | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    run_id = run_id or "scenario-playbook-" + generated_at.replace(":", "-")
    scenario_checks = [_scenario_check(scenario) for scenario in SCENARIOS]
    report = {
        "status": "ok" if all(check["mvp_complete"] for check in scenario_checks) else "failed",
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
            "real_private_network_connected": False,
            "production_traffic": False,
        },
        "core_mvp_nodes": CORE_MVP_NODES,
        "scenarios": scenario_checks,
        "metrics": _metrics(scenario_checks),
        "adoption_thresholds": _adoption_thresholds(),
        "boundary": {
            "can_say": [
                "DataOps 的 MVP 可以迁移到企业分析、客服知识库、商品目录和内容知识治理等场景。",
                "跨场景复用的是来源、合同、质量、新鲜度、审批、发布、回归、审计和回滚主链路。",
                "医院 Agent 是第一个下游消费者案例，不是 DataOps 的唯一业务场景。",
            ],
            "cannot_say": [
                "不能说已经接入真实企业数据、真实客服系统、真实电商系统或真实内容平台。",
                "不能说已经完成生产级数据目录、实时流处理、A2A、消息队列或多租户权限中心。",
                "不能把开放场景设计说成真实业务 ROI、真实用户流量或生产 SLA。",
            ],
        },
    }
    _write_json(report_json, report)
    _write_markdown(report_md, report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate DataOps open-scenario playbook.")
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    report = run_scenario_playbook(
        report_json=args.report_json,
        report_md=args.report_md,
        run_id=args.run_id,
    )
    print(json.dumps(_stdout_summary(report), ensure_ascii=False, indent=2))
    if report["status"] != "ok":
        raise SystemExit(1)


def _scenario_check(scenario: dict[str, Any]) -> dict[str, Any]:
    mvp_nodes = list(scenario["mvp_nodes"])
    missing_core_nodes = [node for node in CORE_MVP_NODES if node not in mvp_nodes]
    return {
        **scenario,
        "missing_core_nodes": missing_core_nodes,
        "mvp_complete": not missing_core_nodes,
        "risk_count": len(scenario["top_risks"]),
        "control_count": len(scenario["dataops_controls"]),
    }


def _metrics(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "scenario_count": len(scenarios),
        "scenario_with_complete_mvp_count": sum(1 for item in scenarios if item["mvp_complete"]),
        "core_mvp_node_count": len(CORE_MVP_NODES),
        "total_risk_count": sum(item["risk_count"] for item in scenarios),
        "total_control_count": sum(item["control_count"] for item in scenarios),
        "new_dependency_required": False,
        "new_framework_required": False,
    }


def _adoption_thresholds() -> list[dict[str, str]]:
    return [
        {
            "capability": "Airflow/Kafka/production scheduler",
            "adopt_when": "多源定时、依赖编排、失败重试、实时刷新和生产告警成为主矛盾。",
            "current_choice": "当前保留 freshness 检查和 dry-run 调度记录。",
        },
        {
            "capability": "DataHub/OpenLineage/enterprise catalog",
            "adopt_when": "多团队、多数据集、多版本影响分析和列级血缘审计成为主矛盾。",
            "current_choice": "当前保留数据合同、manifest、checksum 和轻量血缘口径。",
        },
        {
            "capability": "A2A/message bus/multi-agent scheduler",
            "adopt_when": "多个 Agent 需要并发委派、异步事件流、订阅回执和组织级权限审计。",
            "current_choice": (
                "当前使用 HTTP/JSON consumer contract、MCP 控制面和 trace/thread 审计。"
            ),
        },
        {
            "capability": "production RBAC/DLP/SIEM",
            "adopt_when": "出现真实组织账号、敏感数据、审计留存和合规运营要求。",
            "current_choice": "当前保留本地安全对抗审查、凭证扫描和禁止越权写入。",
        },
    ]


def _write_json(path: Path | str, report: dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_markdown(path: Path | str, report: dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# DataOps Agent 开放场景迁移手册",
        "",
        f"- 运行 ID：`{report['run_id']}`",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 结果：`{report['status']}`",
        "- 范围：本地产品化迁移设计；本轮未启用 ECS，未调用 Qwen，未接入真实私有系统。",
        "",
        "## 核心指标",
        "",
        "| 维度 | 指标 |",
        "|---|---|",
        f"| 开放场景 | {report['metrics']['scenario_count']} 个 |",
        (
            "| MVP 完整性 | "
            f"{report['metrics']['scenario_with_complete_mvp_count']}/"
            f"{report['metrics']['scenario_count']} 场景覆盖核心节点 |"
        ),
        f"| 核心 MVP 节点 | {report['metrics']['core_mvp_node_count']} 个 |",
        f"| 风险与控制 | {report['metrics']['total_risk_count']} 个风险，"
        f"{report['metrics']['total_control_count']} 个治理控制 |",
        "| 新依赖 | new_dependency_required=False，new_framework_required=False |",
        "",
        "## 核心 MVP 节点",
        "",
    ]
    lines.extend(f"- {node}" for node in report["core_mvp_nodes"])
    lines.extend(["", "## 场景迁移矩阵", ""])
    for scenario in report["scenarios"]:
        lines.extend(
            [
                f"### {scenario['title']}",
                "",
                f"- 目标用户：{scenario['target_user']}",
                f"- 下游消费者：{scenario['downstream_consumer']}",
                f"- 北极星指标：{scenario['north_star_metric']}",
                f"- 输入来源：{'、'.join(scenario['input_sources'])}",
                f"- MVP 完整：{scenario['mvp_complete']}",
                "- 最高风险：",
            ]
        )
        lines.extend(f"  - {risk}" for risk in scenario["top_risks"])
        lines.append("- DataOps 控制：")
        lines.extend(f"  - {control}" for control in scenario["dataops_controls"])
        lines.append(f"- MVP 不包含：{'、'.join(scenario['not_in_mvp'])}")
        lines.append("")

    lines.extend(["## 重平台采用阈值", ""])
    lines.extend(
        (
            f"- {item['capability']}：当前选择是{item['current_choice']} "
            f"采用阈值是{item['adopt_when']}"
        )
        for item in report["adoption_thresholds"]
    )
    lines.extend(["", "## 边界", ""])
    lines.extend(f"- 可以说：{item}" for item in report["boundary"]["can_say"])
    lines.extend(f"- 不能说：{item}" for item in report["boundary"]["cannot_say"])
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _stdout_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report["status"],
        "run_id": report["run_id"],
        "scenario_count": report["metrics"]["scenario_count"],
        "scenario_with_complete_mvp_count": report["metrics"][
            "scenario_with_complete_mvp_count"
        ],
        "new_dependency_required": report["metrics"]["new_dependency_required"],
        "qwen_called_this_run": report["scope"]["qwen_called_this_run"],
        "ecs_used_this_run": report["scope"]["ecs_used_this_run"],
    }


if __name__ == "__main__":
    main()
