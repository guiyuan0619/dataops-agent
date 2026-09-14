from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dataops_agent.demo import _demo_paths, _require_files
from dataops_agent.governance import (
    resume_governance_run,
    start_governance_run,
    thread_status,
)
from dataops_agent.rules import load_csv, quality_report
from dataops_agent.versioning import VersionStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "dataops_approval_status_evidence.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "dataops_approval_status_evidence.md"
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT / "reports" / "approval_status_runtime"


def run_approval_status_evidence(
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

    paths = _demo_paths()
    _require_files(paths)

    clean_doctors = load_csv(str(paths["clean_doctors"]))
    baseline_version = store.publish(
        dataset_name="doctors",
        df=clean_doctors,
        quality_report=quality_report(clean_doctors, dataset_name="doctors"),
        approved=True,
    )

    approved_thread_id = f"{run_id}-approved"
    approval_pending = start_governance_run(
        thread_id=approved_thread_id,
        dataset_name="doctors",
        dataset_path=paths["dirty_doctors"],
        checkpoint_path=checkpoint_path,
        version_db_path=store.db_path,
        artifact_dir=store.artifact_dir,
    )
    approval_checkpoint = thread_status(
        thread_id=approved_thread_id,
        checkpoint_path=checkpoint_path,
    )
    published = resume_governance_run(
        thread_id=approved_thread_id,
        approved=True,
        approver="batch_d_reviewer",
        checkpoint_path=checkpoint_path,
    )
    published_active_version_id = store.active_version("doctors").version_id

    denied_thread_id = f"{run_id}-denied"
    version_count_before_denial = len(store.list_versions("doctors"))
    active_version_before_denial = store.active_version("doctors").version_id
    denial_pending = start_governance_run(
        thread_id=denied_thread_id,
        dataset_name="doctors",
        dataset_path=paths["dirty_doctors"],
        checkpoint_path=checkpoint_path,
        version_db_path=store.db_path,
        artifact_dir=store.artifact_dir,
    )
    denied = resume_governance_run(
        thread_id=denied_thread_id,
        approved=False,
        approver="batch_d_reviewer",
        checkpoint_path=checkpoint_path,
    )
    version_count_after_denial = len(store.list_versions("doctors"))
    active_version_after_denial = store.active_version("doctors").version_id

    manual_review = start_governance_run(
        thread_id=f"{run_id}-manual-review",
        dataset_name="hospitals",
        dataset_path=paths["dirty_cms_hospitals"],
        checkpoint_path=checkpoint_path,
        version_db_path=store.db_path,
        artifact_dir=store.artifact_dir,
    )

    incomplete_candidate = runtime_dir / "candidate_missing_active_doctor.csv"
    active_after_publish = store.active_version("doctors")
    if active_after_publish is None:
        raise RuntimeError("Published version was not set as active.")
    active_doctors = load_csv(active_after_publish.path)
    if "status" in active_doctors.columns:
        active_rows = active_doctors[
            active_doctors["status"].astype(str).str.strip().str.lower() == "active"
        ]
        drop_index = active_rows.index[0] if not active_rows.empty else active_doctors.index[0]
    else:
        drop_index = active_doctors.index[0]
    active_doctors.drop(index=drop_index).to_csv(incomplete_candidate, index=False)
    version_count_before_gate_failure = len(store.list_versions("doctors"))
    active_version_before_gate_failure = store.active_version("doctors").version_id
    consumer_gate_failure = start_governance_run(
        thread_id=f"{run_id}-consumer-gate-failure",
        dataset_name="doctors",
        dataset_path=incomplete_candidate,
        checkpoint_path=checkpoint_path,
        version_db_path=store.db_path,
        artifact_dir=store.artifact_dir,
    )
    version_count_after_gate_failure = len(store.list_versions("doctors"))
    active_version_after_gate_failure = store.active_version("doctors").version_id
    missing_key_count = consumer_gate_failure["consumer_regression"]["metrics"][
        "missing_baseline_key_count"
    ]

    status_cases = [
        _status_case(
            name="待审批",
            status=str(approval_pending["status"]),
            trigger="治理图生成修复预览后暂停",
            evidence=(
                f"thread_status={approval_checkpoint['status']}; "
                f"preview_changes={len(approval_pending['interrupt']['preview']['changes'])}"
            ),
            write_effect="未切换 active version",
        ),
        _status_case(
            name="批准",
            status="approved",
            trigger="恢复治理图时显式传入 approved=true",
            evidence=f"approver={published.get('approver')}; final_status={published['status']}",
            write_effect="进入发布门禁，不直接绕过校验",
        ),
        _status_case(
            name="发布成功",
            status=str(published["status"]),
            trigger="checksum、质量门禁和下游回归全部通过",
            evidence=(
                f"version_id={published['version']['version_id']}; "
                f"active_version={published_active_version_id}"
            ),
            write_effect="新版本成为 active version",
        ),
        _status_case(
            name="拒绝",
            status=str(denied["status"]),
            trigger="恢复治理图时显式传入 approved=false",
            evidence=(
                f"pending_status={denial_pending['status']}; "
                f"versions_before={version_count_before_denial}; "
                f"versions_after={version_count_after_denial}; "
                f"active_before={active_version_before_denial}; "
                f"active_after={active_version_after_denial}"
            ),
            write_effect="版本数量不变，active version 不变",
        ),
        _status_case(
            name="人工复核",
            status=str(manual_review["status"]),
            trigger="问题数超过确定性修复计划能力",
            evidence=f"unresolved_issue_count={manual_review.get('unresolved_issue_count', 0)}",
            write_effect="不进入自动发布",
        ),
        _status_case(
            name="门禁失败",
            status=str(consumer_gate_failure["status"]),
            trigger="候选数据缺少已发布版本中的下游关键记录",
            evidence=(
                f"missing_baseline_key_count={missing_key_count}; "
                f"versions_before={version_count_before_gate_failure}; "
                f"versions_after={version_count_after_gate_failure}; "
                f"active_before={active_version_before_gate_failure}; "
                f"active_after={active_version_after_gate_failure}"
            ),
            write_effect="版本数量不变，active version 不变",
        ),
    ]
    gates = {
        "pending_status_visible": approval_pending["status"] == "awaiting_approval",
        "checkpoint_status_restored": approval_checkpoint["status"] == "awaiting_approval",
        "approval_decision_visible": published.get("approved") is True,
        "published_status_visible": published["status"] == "published",
        "denial_status_visible": denied["status"] == "denied"
        and version_count_before_denial == version_count_after_denial
        and active_version_before_denial == active_version_after_denial,
        "manual_review_status_visible": manual_review["status"] == "manual_review_required",
        "consumer_gate_failure_visible": (
            consumer_gate_failure["status"] == "consumer_regression_failed"
            and missing_key_count > 0
            and version_count_before_gate_failure == version_count_after_gate_failure
            and active_version_before_gate_failure == active_version_after_gate_failure
        ),
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
        "baseline": {
            "dataset_name": baseline_version.dataset_name,
            "version_id": baseline_version.version_id,
            "active_version_before_approval": baseline_version.version_id,
        },
        "gates": gates,
        "status_cases": status_cases,
        "interview_boundary": {
            "can_say": [
                "审批状态不是只写在文档里，治理图可以留下待审批 checkpoint 并恢复执行。",
                "批准、拒绝、人工复核、门禁失败和发布成功都有可复现报告证据。",
                (
                    "批准只代表进入发布门禁；真正切 active version 仍要过 "
                    "checksum、质量门禁和下游回归。"
                ),
            ],
            "cannot_say": [
                "这是生产审批后台。",
                "已经接入企业级多角色 RBAC 审批流。",
                "MCP 或 LLM 可以直接审批发布。",
            ],
        },
    }

    _write_report(Path(report_json), report)
    _write_report_markdown(Path(report_md), report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate DataOps approval status evidence reports."
    )
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    report = run_approval_status_evidence(
        report_json=args.report_json,
        report_md=args.report_md,
        runtime_root=args.runtime_root,
        run_id=args.run_id,
    )
    print(json.dumps(_stdout_summary(report), ensure_ascii=False, indent=2))
    if report["status"] != "ok":
        raise SystemExit(1)


def _status_case(
    *,
    name: str,
    status: str,
    trigger: str,
    evidence: str,
    write_effect: str,
) -> dict[str, str]:
    return {
        "name": name,
        "status": status,
        "trigger": trigger,
        "evidence": evidence,
        "write_effect": write_effect,
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_report_markdown(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_markdown_report(report), encoding="utf-8")


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# DataOps Agent 审批状态证据包",
        "",
        f"- 运行 ID：`{report['run_id']}`",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 结果：`{report['status']}`",
        "- 范围：本地离线验证，未启用 ECS，未调用 Qwen，非生产流量。",
        "",
        "## 状态总览",
        "",
        "| 面试可见状态 | 系统状态 | 触发方式 | 可见证据 | 写入影响 |",
        "|---|---|---|---|---|",
    ]
    for item in report["status_cases"]:
        lines.append(
            "| {name} | `{status}` | {trigger} | {evidence} | {write_effect} |".format(
                **item
            )
        )
    lines.extend(
        [
            "",
            "## 验收门禁",
            "",
        ]
    )
    lines.extend(f"- {name}: `{value}`" for name, value in report["gates"].items())
    lines.extend(
        [
            "",
            "## 面试口径",
            "",
            (
                "- 可以说：审批不是口头设计，代码能复现待审批、批准、拒绝、"
                "人工复核、门禁失败和发布成功。"
            ),
            (
                "- 可以说：批准只代表进入发布链路，真正切换版本还要过 checksum、"
                "质量门禁和下游回归。"
            ),
            (
                "- 不要说：已经做了生产审批后台、企业级多角色 RBAC，"
                "或让 MCP/LLM 直接审批发布。"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _stdout_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report["status"],
        "run_id": report["run_id"],
        "status_cases": [item["name"] for item in report["status_cases"]],
        "gates_passed": all(report["gates"].values()),
        "report_json": report["outputs"]["report_json"],
        "report_md": report["outputs"]["report_md"],
    }


def _safe_run_id(value: str) -> str:
    return "approval-" + "".join(char if char.isalnum() else "-" for char in value).strip("-")


if __name__ == "__main__":
    main()
