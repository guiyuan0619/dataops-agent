from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dataops_agent.acceptance import run_acceptance

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "dataops_training_candidates.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "dataops_training_candidates.md"
DEFAULT_CANDIDATES_JSONL = PROJECT_ROOT / "reports" / "dataops_training_candidates.jsonl"
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT / "reports" / "training_candidates_runtime"

CASE_TRAINING_ROUTES = {
    "source_manifest_traceable": {
        "failure_type": "source_traceability",
        "preferred_fix_route": "data_contract_or_manifest_gate",
        "direct_sft_candidate": False,
        "preference_candidate": False,
        "reason": "根因是来源和元数据可信边界，不应靠模型训练修复。",
    },
    "unapproved_publish_blocked": {
        "failure_type": "unauthorized_write",
        "preferred_fix_route": "approval_gate",
        "direct_sft_candidate": False,
        "preference_candidate": False,
        "reason": "根因是写权限边界，必须由审批门禁控制。",
    },
    "dirty_candidate_blocks_consumer_regression": {
        "failure_type": "candidate_quality_failure",
        "preferred_fix_route": "quality_rule_or_manual_review",
        "direct_sft_candidate": False,
        "preference_candidate": False,
        "reason": "根因是结构化数据质量问题，优先修规则、合同和候选数据。",
    },
    "missing_baseline_key_blocks_release": {
        "failure_type": "downstream_regression_failure",
        "preferred_fix_route": "consumer_regression_gate",
        "direct_sft_candidate": False,
        "preference_candidate": False,
        "reason": "根因是下游可见 key 丢失，必须由回归门禁拦截。",
    },
    "unresolved_public_data_issues_require_manual_review": {
        "failure_type": "ambiguous_public_data_issue",
        "preferred_fix_route": "human_review_queue",
        "direct_sft_candidate": False,
        "preference_candidate": True,
        "reason": "可用于未来训练模型解释为什么进入人工复核，但不能直接替代复核。",
    },
    "candidate_checksum_mismatch_blocks_publish": {
        "failure_type": "candidate_changed_after_approval",
        "preferred_fix_route": "checksum_revalidation_gate",
        "direct_sft_candidate": False,
        "preference_candidate": False,
        "reason": "根因是候选文件在审批后变化，必须由 checksum 复核拦截。",
    },
    "unapproved_rollback_does_not_switch_active_version": {
        "failure_type": "unauthorized_rollback",
        "preferred_fix_route": "rollback_approval_gate",
        "direct_sft_candidate": False,
        "preference_candidate": False,
        "reason": "根因是回滚权限边界，必须由审批门禁控制。",
    },
}


def run_training_candidate_export(
    *,
    report_json: Path | str = DEFAULT_REPORT_JSON,
    report_md: Path | str = DEFAULT_REPORT_MD,
    candidates_jsonl: Path | str = DEFAULT_CANDIDATES_JSONL,
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
    records = [
        _candidate_record(run_id, case, acceptance["outputs"]) for case in acceptance["cases"]
    ]
    metrics = _metrics(records)
    report = {
        "status": "ok" if acceptance["status"] == "ok" else "failed",
        "run_id": run_id,
        "generated_at": generated_at,
        "runtime_dir": str(runtime_dir),
        "outputs": {
            "report_json": str(Path(report_json)),
            "report_md": str(Path(report_md)),
            "candidates_jsonl": str(Path(candidates_jsonl)),
        },
        "scope": {
            "local_only": True,
            "ecs_used_this_run": False,
            "qwen_called_this_run": False,
            "production_traffic": False,
            "model_training_executed": False,
            "human_labeling_required": True,
        },
        "source_reports": {
            "acceptance": acceptance["outputs"],
        },
        "metrics": metrics,
        "records": records,
        "boundary": {
            "can_say": [
                "评测和反例库已经能导出训练候选池，记录失败类型、修复路线和是否适合训练。",
                "当前导出证明了数据飞轮治理能力，而不是已经完成模型微调。",
                "大多数 DataOps 失败应优先走规则、合同、审批、回归或人工复核，不应直接 SFT。",
            ],
            "cannot_say": [
                "不能说已经完成 SFT、DPO、RLHF、偏好学习或在线训练。",
                "不能说所有 badcase 都适合训练模型解决。",
                "不能说这些样本已经是可直接训练的人工标注数据集。",
            ],
        },
    }
    _write_json(report_json, report)
    _write_jsonl(candidates_jsonl, records)
    _write_markdown(report_md, report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Export DataOps Agent badcase records into a training-candidate pool."
    )
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--candidates-jsonl", default=str(DEFAULT_CANDIDATES_JSONL))
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    report = run_training_candidate_export(
        report_json=args.report_json,
        report_md=args.report_md,
        candidates_jsonl=args.candidates_jsonl,
        runtime_root=args.runtime_root,
        run_id=args.run_id,
    )
    print(json.dumps(_stdout_summary(report), ensure_ascii=False, indent=2))
    if report["status"] != "ok":
        raise SystemExit(1)


def _candidate_record(
    run_id: str,
    case: dict[str, Any],
    acceptance_outputs: dict[str, str],
) -> dict[str, Any]:
    route = CASE_TRAINING_ROUTES[case["name"]]
    return {
        "candidate_id": f"{run_id}:{case['name']}",
        "source_project": "dataops_agent",
        "source_report_type": "acceptance_counterexample",
        "source_report_json": acceptance_outputs["report_json"],
        "source_case": case["name"],
        "stage": case["stage"],
        "risk": case["risk"],
        "failure_type": route["failure_type"],
        "observed": case["observed"],
        "expected": case["expected"],
        "guardrail": case["guardrail"],
        "preferred_fix_route": route["preferred_fix_route"],
        "model_training_decision": {
            "direct_sft_candidate": route["direct_sft_candidate"],
            "preference_candidate": route["preference_candidate"],
            "requires_human_labeling": True,
            "reason": route["reason"],
        },
        "future_label_schema": {
            "user_or_task_input": "待人工补充真实问题或任务描述",
            "context": {
                "stage": case["stage"],
                "risk": case["risk"],
                "evidence": case["evidence"],
            },
            "expected_behavior": [
                "先识别失败类型和风险边界",
                "说明优先修复路线",
                "不能绕过审批、质量门禁或人工复核",
            ],
            "safety_labels": [
                route["failure_type"],
                route["preferred_fix_route"],
            ],
        },
        "split": "candidate_pool",
        "not_for_direct_training": True,
    }


def _metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    direct_sft = [
        record
        for record in records
        if record["model_training_decision"]["direct_sft_candidate"]
    ]
    preference = [
        record for record in records if record["model_training_decision"]["preference_candidate"]
    ]
    deterministic = [
        record
        for record in records
        if not record["model_training_decision"]["direct_sft_candidate"]
    ]
    return {
        "candidate_record_count": len(records),
        "direct_sft_candidate_count": len(direct_sft),
        "preference_candidate_count": len(preference),
        "deterministic_fix_first_count": len(deterministic),
        "requires_human_labeling_count": sum(
            1 for record in records if record["model_training_decision"]["requires_human_labeling"]
        ),
        "failure_types": sorted({record["failure_type"] for record in records}),
        "preferred_fix_routes": sorted({record["preferred_fix_route"] for record in records}),
    }


def _write_json(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path | str, records: list[dict[str, Any]]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_markdown(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    metrics = report["metrics"]
    lines = [
        "# DataOps Agent 训练候选导出报告",
        "",
        f"- 运行 ID：`{report['run_id']}`",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 结果：`{report['status']}`",
        "- 范围：本地导出评测候选池；未启用 ECS，未调用 Qwen，未执行模型训练。",
        "",
        "## 指标",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 候选记录数 | {metrics['candidate_record_count']} |",
        f"| 直接 SFT 候选数 | {metrics['direct_sft_candidate_count']} |",
        f"| 偏好学习候选数 | {metrics['preference_candidate_count']} |",
        f"| 优先确定性修复数 | {metrics['deterministic_fix_first_count']} |",
        f"| 需要人工标注数 | {metrics['requires_human_labeling_count']} |",
        "",
        "## 候选记录",
        "",
        "| 来源 case | 失败类型 | 优先修复路线 | 直接 SFT | 偏好候选 |",
        "|---|---|---|---:|---:|",
    ]
    for record in report["records"]:
        decision = record["model_training_decision"]
        lines.append(
            "| "
            f"{record['source_case']} | "
            f"{record['failure_type']} | "
            f"{record['preferred_fix_route']} | "
            f"{decision['direct_sft_candidate']} | "
            f"{decision['preference_candidate']} |"
        )
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "- 可以说：评测反例已经能导出为训练候选池，并标注失败类型、修复路线和训练适配性。",
            (
                "- 可以说：当前 7 条候选全部需要人工标注，且多数应优先走确定性规则、"
                "合同、审批或回归门禁。"
            ),
            "- 不能说：已完成 SFT、DPO、RLHF、偏好学习、在线训练或可直接训练的数据集。",
            "",
        ]
    )
    destination.write_text("\n".join(lines), encoding="utf-8")


def _stdout_summary(report: dict[str, Any]) -> dict[str, Any]:
    metrics = report["metrics"]
    return {
        "status": report["status"],
        "run_id": report["run_id"],
        "candidate_record_count": metrics["candidate_record_count"],
        "direct_sft_candidate_count": metrics["direct_sft_candidate_count"],
        "preference_candidate_count": metrics["preference_candidate_count"],
        "deterministic_fix_first_count": metrics["deterministic_fix_first_count"],
        "model_training_executed": report["scope"]["model_training_executed"],
        "qwen_called_this_run": report["scope"]["qwen_called_this_run"],
        "ecs_used_this_run": report["scope"]["ecs_used_this_run"],
        "report_json": report["outputs"]["report_json"],
        "report_md": report["outputs"]["report_md"],
        "candidates_jsonl": report["outputs"]["candidates_jsonl"],
    }


def _safe_run_id(value: str) -> str:
    return "training-candidates-" + "".join(
        char if char.isalnum() else "-" for char in value
    ).strip("-")


if __name__ == "__main__":
    main()
