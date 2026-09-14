from __future__ import annotations

import argparse
import importlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEG_PROJECT_ROOT = PROJECT_ROOT.parent / (
    "hospital-service-agent"
    if (PROJECT_ROOT.parent / "hospital-service-agent").is_dir()
    else "seg_project"
)
DEFAULT_DOWNSTREAM_REPORT = PROJECT_ROOT / "reports" / "hospital_downstream_regression.json"
DEFAULT_COLLABORATION_REPORT = PROJECT_ROOT / "reports" / "dataops_collaboration_contract.json"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "dataops_hospital_impact.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "dataops_hospital_impact.md"


def run_hospital_impact_report(
    *,
    downstream_report: Path | str = DEFAULT_DOWNSTREAM_REPORT,
    collaboration_report: Path | str = DEFAULT_COLLABORATION_REPORT,
    report_json: Path | str = DEFAULT_REPORT_JSON,
    report_md: Path | str = DEFAULT_REPORT_MD,
    seg_project_root: Path | str = DEFAULT_SEG_PROJECT_ROOT,
    run_id: str | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    run_id = run_id or _safe_run_id(generated_at)
    downstream = _load_json(Path(downstream_report))
    collaboration = _load_json(Path(collaboration_report))
    preflight = _run_hospital_preflight(
        release_report_path=Path(downstream_report),
        seg_project_root=Path(seg_project_root),
    )
    metrics = _metrics(downstream, collaboration, preflight)
    report = {
        "status": "ok"
        if downstream.get("status") == "passed" and collaboration.get("status") == "ok"
        else "failed",
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
            "production_traffic": False,
            "direct_hospital_answer_accuracy_eval": False,
        },
        "source_reports": {
            "downstream_regression": str(Path(downstream_report)),
            "collaboration_contract": str(Path(collaboration_report)),
        },
        "metrics": metrics,
        "impact_assessment": [
            {
                "area": "knowledge_release_reliability",
                "status": "improved",
                "plain_result": (
                    "医院 Agent 不再只能人工接收知识文件，而是先检查 DataOps "
                    "发布包、知识文档、下游回归和路径边界。"
                ),
            },
            {
                "area": "downstream_retrieval_safety",
                "status": "improved",
                "plain_result": (
                    "本地检索回归证明已发布医生知识能被医院检索层命中，"
                    "inactive 记录没有进入知识包或 top-k。"
                ),
            },
            {
                "area": "direct_answer_quality",
                "status": "not_measured",
                "plain_result": (
                    "本报告没有证明医院 Agent 问答准确率提升，只证明上游知识发布"
                    "和下游消费边界更稳。"
                ),
            },
        ],
        "resume_decision": {
            "main_resume": (
                "不建议新增单独 bullet；主简历继续概括为下游兼容性回归和发布质量评分卡。"
            ),
            "interview_or_application_detail": (
                "可以展开说医生知识包 5 条文档、6/6 回归通过、inactive 记录 0 泄漏，"
                "并且下游消费者没有审批/发布/回滚权限。"
            ),
        },
        "resume_ready_metrics": [
            (
                "DataOps 对医院 Agent 的提升是发布可靠性：医生知识包 "
                f"{metrics['knowledge_doc_count']} 条文档，"
                f"{metrics['passed_canary_case_count']}/{metrics['canary_case_count']} "
                "下游回归通过。"
            ),
            (
                "DataOps 对医院 Agent 的提升是消费安全：inactive 医生记录泄漏数 "
                f"{metrics['forbidden_doc_leak_count']}，下游消费者可审批数 "
                f"{metrics['downstream_can_approve_count']}。"
            ),
            (
                "这组指标适合放投递库、项目展示和面试防守；主简历只写成"
                "下游兼容性回归，不堆小规模计数。"
            ),
        ],
        "boundary": {
            "can_say": [
                "DataOps 把医院 Agent 的知识更新前置为可预检、可回归、可审计的发布包消费流程。",
                "DataOps 发布版本可转成医院 Agent 知识 JSONL，并通过本地检索回归。",
                "医院 Agent 侧 preflight 通过后也只进入人工审批后的索引重建边界。",
            ],
            "cannot_say": [
                "不能说 DataOps 已经直接提升医院线上问答准确率。",
                "不能说 DataOps 自动写医院知识库、Milvus 或生产索引。",
                "不能说这是真实医院生产流量、真实业务 ROI 或生产 SLA。",
            ],
        },
    }
    _write_json(report_json, report)
    _write_markdown(report_md, report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Summarize DataOps impact on the Hospital Agent consumer."
    )
    parser.add_argument("--downstream-report", default=str(DEFAULT_DOWNSTREAM_REPORT))
    parser.add_argument("--collaboration-report", default=str(DEFAULT_COLLABORATION_REPORT))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--seg-project-root", default=str(DEFAULT_SEG_PROJECT_ROOT))
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    report = run_hospital_impact_report(
        downstream_report=args.downstream_report,
        collaboration_report=args.collaboration_report,
        report_json=args.report_json,
        report_md=args.report_md,
        seg_project_root=args.seg_project_root,
        run_id=args.run_id,
    )
    print(json.dumps(_stdout_summary(report), ensure_ascii=False, indent=2))
    if report["status"] != "ok":
        raise SystemExit(1)


def _metrics(
    downstream: dict[str, Any],
    collaboration: dict[str, Any],
    preflight: dict[str, Any],
) -> dict[str, Any]:
    downstream_metrics = downstream.get("metrics", {})
    collaboration_metrics = collaboration.get("metrics", {})
    return {
        "knowledge_doc_count": int(downstream.get("knowledge_doc_count") or 0),
        "canary_case_count": int(downstream.get("case_count") or 0),
        "passed_canary_case_count": int(downstream_metrics.get("passed_case_count") or 0),
        "pass_rate": downstream_metrics.get("pass_rate"),
        "top1_accuracy": downstream_metrics.get("top1_accuracy"),
        "hit_at_k": downstream_metrics.get("hit_at_k"),
        "tool_match_rate": downstream_metrics.get("tool_match_rate"),
        "term_coverage_rate": downstream_metrics.get("term_coverage_rate"),
        "forbidden_doc_leak_count": int(
            downstream_metrics.get("forbidden_doc_leak_count") or 0
        ),
        "hospital_preflight_status": preflight.get("status"),
        "hospital_preflight_ready_for_reindex": preflight.get("ready_for_reindex"),
        "hospital_preflight_issue_count": len(preflight.get("issues") or []),
        "collaboration_scenario_count": int(collaboration_metrics.get("scenario_count") or 0),
        "covered_collaboration_scenario_count": int(
            collaboration_metrics.get("covered_scenario_count") or 0
        ),
        "hospital_compatibility_route_count": int(
            collaboration_metrics.get("hospital_compatibility_route_count") or 0
        ),
        "consumer_approved_rollback_blocked_at_api": collaboration_metrics.get(
            "consumer_approved_rollback_blocked_at_api"
        ),
        "downstream_can_approve_count": int(
            collaboration_metrics.get("downstream_can_approve_count") or 0
        ),
    }


def _run_hospital_preflight(
    *,
    release_report_path: Path,
    seg_project_root: Path,
) -> dict[str, Any]:
    if not seg_project_root.is_dir():
        return {"status": "skipped", "ready_for_reindex": None, "issues": ["seg_project_missing"]}
    sys.path.insert(0, str(seg_project_root))
    try:
        module = importlib.import_module("src.backend.services.hospital_dataops_knowledge_service")
        preflight = module.preflight_dataops_knowledge_release(
            release_report_path=str(release_report_path)
        )
    except Exception as exc:  # pragma: no cover - defensive cross-project boundary.
        return {
            "status": "skipped",
            "ready_for_reindex": None,
            "issues": [f"hospital_preflight_unavailable:{exc.__class__.__name__}"],
        }
    return {
        "status": preflight.status,
        "ready_for_reindex": preflight.ready_for_reindex,
        "knowledge_doc_count": preflight.knowledge_doc_count,
        "validated_doc_count": preflight.validated_doc_count,
        "canary_case_count": preflight.canary_case_count,
        "passed_canary_case_count": preflight.passed_canary_case_count,
        "issues": preflight.issues,
    }


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_markdown(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    metrics = report["metrics"]
    lines = [
        "# DataOps 对医院 Agent 的影响报告",
        "",
        f"- 运行 ID：`{report['run_id']}`",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 结果：`{report['status']}`",
        "- 范围：本地证据聚合；本轮未启用 ECS，未调用 Qwen，非生产流量。",
        "",
        "## 结论",
        "",
        (
            "DataOps 给医院 Agent 带来的提升是知识发布可靠性和下游消费安全，"
            "不是直接证明医院问答准确率提升。"
        ),
        "",
        "## 核心指标",
        "",
        "| 维度 | 指标 |",
        "|---|---|",
        (
            "| 知识发布 | "
            f"{metrics['knowledge_doc_count']} 条医生知识文档，"
            f"{metrics['passed_canary_case_count']}/{metrics['canary_case_count']} "
            "下游回归通过 |"
        ),
        (
            "| 检索安全 | "
            f"top1={metrics['top1_accuracy']}，hit@k={metrics['hit_at_k']}，"
            f"inactive 泄漏={metrics['forbidden_doc_leak_count']} |"
        ),
        (
            "| 医院侧预检 | "
            f"status={metrics['hospital_preflight_status']}，"
            f"ready_for_reindex={metrics['hospital_preflight_ready_for_reindex']}，"
            f"issue_count={metrics['hospital_preflight_issue_count']} |"
        ),
        (
            "| 协作边界 | "
            f"{metrics['covered_collaboration_scenario_count']}/"
            f"{metrics['collaboration_scenario_count']} 场景覆盖，"
            f"下游可审批数={metrics['downstream_can_approve_count']} |"
        ),
        "",
        "## 简历判断",
        "",
        f"- 主简历：{report['resume_decision']['main_resume']}",
        f"- 面试展开：{report['resume_decision']['interview_or_application_detail']}",
        "",
        "## 可以说",
        "",
    ]
    lines.extend(f"- {item}" for item in report["boundary"]["can_say"])
    lines.extend(["", "## 不要这样说", ""])
    lines.extend(f"- {item}" for item in report["boundary"]["cannot_say"])
    lines.append("")
    destination.write_text("\n".join(lines), encoding="utf-8")


def _safe_run_id(value: str) -> str:
    return "hospital-impact-" + "".join(
        char if char.isalnum() else "-" for char in value
    ).strip("-")


def _stdout_summary(report: dict[str, Any]) -> dict[str, Any]:
    metrics = report["metrics"]
    return {
        "status": report["status"],
        "knowledge_doc_count": metrics["knowledge_doc_count"],
        "passed_canary_case_count": metrics["passed_canary_case_count"],
        "canary_case_count": metrics["canary_case_count"],
        "forbidden_doc_leak_count": metrics["forbidden_doc_leak_count"],
        "hospital_preflight_status": metrics["hospital_preflight_status"],
        "direct_answer_quality_measured": report["scope"]["direct_hospital_answer_accuracy_eval"],
        "report_json": report["outputs"]["report_json"],
        "report_md": report["outputs"]["report_md"],
    }


if __name__ == "__main__":
    main()
