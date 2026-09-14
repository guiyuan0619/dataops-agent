from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dataops_agent.cross_project_recovery import run_cross_project_recovery_drill
from dataops_agent.demo import run_local_demo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_portfolio_demo.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_portfolio_demo.md"
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT / "reports" / "portfolio_demo_runtime"
DEFAULT_DATAOPS_LIVE_REPORT = (
    PROJECT_ROOT
    / "reports"
    / "dataops_semantic_planner_p0_eval_20260912_token_plan_full24_v2_enum_meanings_metrics.json"
)
HOSPITAL_LIVE_REPORT_RELATIVE = Path(
    "experiments/hospital_agent_min_experiment/reports/"
    "hospital_service_agent_p0_eval_20260912_token_plan_full24_v3_candidate_anchor_metrics.json"
)

HospitalRunner = Callable[[Path, Path, Path], dict[str, Any]]
DataOpsRunner = Callable[..., dict[str, Any]]


class PortfolioDemoError(RuntimeError):
    pass


def run_portfolio_demo(
    *,
    hospital_repo: Path | str,
    report_json: Path | str = DEFAULT_REPORT_JSON,
    report_md: Path | str = DEFAULT_REPORT_MD,
    runtime_root: Path | str = DEFAULT_RUNTIME_ROOT,
    run_id: str | None = None,
    hospital_live_report: Path | str | None = None,
    dataops_live_report: Path | str = DEFAULT_DATAOPS_LIVE_REPORT,
    hospital_runner: HospitalRunner | None = None,
    dataops_runner: DataOpsRunner = run_local_demo,
    recovery_runner: DataOpsRunner = run_cross_project_recovery_drill,
) -> dict[str, Any]:
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    run_id = _safe_run_id(generated_at) if run_id is None else run_id
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", run_id):
        raise PortfolioDemoError("run_id must contain only ASCII letters, digits, '_' or '-'")
    hospital_repo_path = Path(hospital_repo).resolve()
    runtime_dir = _create_unique_runtime_dir(Path(runtime_root), run_id)

    with _without_live_model_credentials():
        dataops = dataops_runner(
            report_json=runtime_dir / "dataops_demo.json",
            report_md=runtime_dir / "dataops_demo.md",
            runtime_root=runtime_dir / "dataops_runtime",
            run_id=f"{run_id}-dataops",
        )
    release_report = Path(
        dataops.get("doctor_dataset", {})
        .get("downstream_hospital_agent_report", {})
        .get("report_json", "")
    )
    hospital = (hospital_runner or _run_hospital_api_demo)(
        hospital_repo_path, runtime_dir / "hospital", release_report
    )
    with _without_live_model_credentials():
        recovery = recovery_runner(
            report_json=runtime_dir / "cross_project_recovery.json",
            report_md=runtime_dir / "cross_project_recovery.md",
            runtime_root=runtime_dir / "recovery_runtime",
            run_id=f"{run_id}-recovery",
        )
    live_evidence = _load_live_evidence(
        hospital_repo=hospital_repo_path,
        hospital_report=(
            Path(hospital_live_report)
            if hospital_live_report is not None
            else hospital_repo_path / HOSPITAL_LIVE_REPORT_RELATIVE
        ),
        dataops_report=Path(dataops_live_report),
    )

    components = {
        "hospital_http": _compact_hospital(hospital),
        "dataops_governance": _compact_dataops(dataops),
        "cross_project_recovery": _compact_recovery(recovery),
        "live_model_evidence": live_evidence,
    }
    gates = _acceptance_gates(components)
    report: dict[str, Any] = {
        "status": "ok" if all(gates.values()) else "failed",
        "run_id": run_id,
        "generated_at": generated_at,
        "scope": {
            "local_only": True,
            "real_http_used": True,
            "qwen_called_this_run": False,
            "ecs_used_this_run": False,
            "production_traffic": False,
            "hospital_system_connected": False,
            "hospital_consumed_dataops_release_via_http": gates[
                "hospital_consumes_dataops_release_via_http"
            ],
            "external_actions_executed": False,
        },
        "outputs": {
            "report_json": _relative_reference(Path(report_json), PROJECT_ROOT),
            "report_md": _relative_reference(Path(report_md), PROJECT_ROOT),
        },
        "components": components,
        "gates": gates,
        "public_output_audit": {},
        "boundary": {
            "can_say": [
                "一个命令可复现 DataOps 发布报告经医院 HTTP 预检、服务端会话记忆和跨项目恢复演示。",
                "真实模型质量引用固定 Token Plan 独立评测，本地演示无需密钥。",
                "坏候选在 active 切换前被阻断，医院消费者不能自行执行回滚。",
            ],
            "cannot_say": [
                "不能说演示连接了真实医院系统、执行了真实挂号缴费或使用了生产流量。",
            "不能说本轮演示重新调用了 Qwen 或验证了生产 SLA。",
            "医院预检不核查 DataOps 当前 active 版本；正式切索引前仍须重查版本与人工批准。",
                "不能把本地可复现等同于 GitHub 公开仓库已经完成发布。",
            ],
        },
    }
    audit = _audit_public_text(json.dumps(report, ensure_ascii=False))
    report["public_output_audit"] = audit
    report["gates"]["public_output_safe"] = audit["passed"]
    report["status"] = "ok" if all(report["gates"].values()) else "failed"

    _write_json(Path(report_json), report)
    _write_markdown(Path(report_md), report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the two-Agent portfolio demo.")
    parser.add_argument(
        "--hospital-repo",
        default=str(_default_hospital_repo()),
        help="Path to the sibling hospital Agent repository.",
    )
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    report = run_portfolio_demo(
        hospital_repo=args.hospital_repo,
        report_json=args.report_json,
        report_md=args.report_md,
        runtime_root=args.runtime_root,
        run_id=args.run_id,
    )
    print(json.dumps(_stdout_summary(report), ensure_ascii=False, indent=2))
    if report["status"] != "ok":
        raise SystemExit(1)


def _run_hospital_api_demo(
    hospital_repo: Path, output_dir: Path, release_report: Path
) -> dict[str, Any]:
    script = (
        hospital_repo
        / "experiments"
        / "hospital_agent_min_experiment"
        / "run_p3_agent_api_smoke.py"
    )
    if not script.is_file():
        raise PortfolioDemoError(f"hospital demo script is missing: {script.name}")
    python = _hospital_python(hospital_repo)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = "hospital_agent_portfolio_demo"
    env = os.environ.copy()
    env.update(
        {
            "HOSPITAL_AGENT_RESPONSE_PROVIDER": "local_template",
            "HOSPITAL_AGENT_PLANNER_MODE": "disabled",
            "HOSPITAL_AGENT_LLM_API_KEY_ENV": "HOSPITAL_AGENT_DEMO_DISABLED_API_KEY",
            "HOSPITAL_DATAOPS_REPORTS_DIR": str(release_report.parent.resolve()),
        }
    )
    env.pop("HOSPITAL_AGENT_DEMO_DISABLED_API_KEY", None)
    env.pop("HOSPITAL_AGENT_LLM_API_KEY", None)
    env.pop("DATAOPS_QWEN_API_KEY", None)
    completed = subprocess.run(
        [
            str(python),
            str(script),
            "--self-host",
            "--report-prefix",
            prefix,
            "--report-dir",
            str(output_dir),
            "--dataops-release-report",
            str(release_report),
        ],
        cwd=hospital_repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stdout + "\n" + completed.stderr).strip()[-1000:]
        raise PortfolioDemoError(f"hospital HTTP demo failed: {detail}")
    report_path = output_dir / f"{prefix}.json"
    return _read_json(report_path)


def _hospital_python(hospital_repo: Path) -> Path:
    configured = os.environ.get("HOSPITAL_AGENT_PYTHON")
    if configured:
        candidate = Path(configured).resolve()
        if not candidate.is_file():
            raise PortfolioDemoError("HOSPITAL_AGENT_PYTHON does not point to a Python executable")
        return candidate
    candidates = [
        hospital_repo / ".venv" / "Scripts" / "python.exe",
        hospital_repo / ".venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise PortfolioDemoError(
        "hospital Python environment is missing; install hospital-service-agent/.venv "
        "or set HOSPITAL_AGENT_PYTHON"
    )


def _load_live_evidence(
    *, hospital_repo: Path, hospital_report: Path, dataops_report: Path
) -> dict[str, Any]:
    hospital = _read_json(hospital_report)
    dataops = _read_json(dataops_report)
    hospital_metrics = hospital.get("headline_metrics", {})
    dataops_metrics = dataops.get("headline_metrics", {})
    return {
        "status": (
            "passed"
            if hospital.get("status") == "passed" and dataops.get("status") == "passed"
            else "failed"
        ),
        "hospital": {
            "prompt_version": hospital.get("versions", {}).get("prompt_version"),
            "passed_count": hospital_metrics.get("passed_count"),
            "case_count": hospital_metrics.get("case_count"),
            "pre_gate_model_call_count": hospital_metrics.get(
                "pre_gate_model_call_count"
            ),
            "external_action_execution_count": hospital_metrics.get(
                "external_action_execution_count"
            ),
            "provider_error_count": hospital_metrics.get("provider_error_count"),
            "report": _relative_reference(hospital_report, hospital_repo),
        },
        "dataops": {
            "prompt_version": dataops.get("versions", {}).get("prompt_version"),
            "passed_count": dataops_metrics.get("passed_count"),
            "case_count": dataops_metrics.get("case_count"),
            "mapping_accuracy": dataops_metrics.get("mapping_accuracy"),
            "manual_review_accuracy": dataops_metrics.get("manual_review_accuracy"),
            "approval_bypass_count": dataops_metrics.get("approval_bypass_count"),
            "provider_error_count": dataops_metrics.get("provider_error_count"),
            "report": _relative_reference(dataops_report, PROJECT_ROOT),
        },
    }


def _compact_hospital(report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary", {})
    preflight = report.get("dataops_preflight") or {}
    return {
        "status": "passed" if (
            summary.get("chat_full_trace")
            and summary.get("idempotency_replayed")
            and summary.get("conversation_context_reused")
            and summary.get("stale_version_blocked")
            and summary.get("conversation_audit_events_recorded")
            and summary.get("action_intent_status")
            == "awaiting_hospital_authorization"
            and summary.get("external_execution_status") == "not_connected"
        ) else "failed",
        "chat_case_count": summary.get("chat_case_count"),
        "chat_full_trace": summary.get("chat_full_trace"),
        "conversation_turn_count": summary.get("conversation_turn_count"),
        "conversation_context_reused": summary.get("conversation_context_reused"),
        "stale_version_blocked": summary.get("stale_version_blocked"),
        "action_intent_status": summary.get("action_intent_status"),
        "idempotency_replayed": summary.get("idempotency_replayed"),
        "external_execution_status": summary.get("external_execution_status"),
        "dataops_release_preflight_status": preflight.get("status"),
        "dataops_release_preflight_ready": preflight.get("ready_for_reindex"),
        "dataops_release_doc_count": preflight.get("knowledge_doc_count"),
        "dataops_release_canary_passed_count": preflight.get("passed_canary_case_count"),
        "dataops_active_version_checked": preflight.get("boundary", {}).get(
            "current_active_version_checked"
        ),
    }


def _compact_dataops(report: dict[str, Any]) -> dict[str, Any]:
    gates = report.get("gates", {})
    return {
        "status": "passed" if report.get("status") == "ok" and all(gates.values()) else "failed",
        "gate_count": len(gates),
        "passed_gate_count": sum(bool(value) for value in gates.values()),
        "governance_final_status": report.get("doctor_dataset", {}).get(
            "governance_final_status"
        ),
        "cross_domain_consumer_regression": report.get(
            "cross_domain_consumer_regression", {}
        ).get("status"),
        "trace_event_count": report.get("trace", {}).get("event_count"),
        "qwen_used": report.get("scope", {}).get("qwen_used"),
    }


def _compact_recovery(report: dict[str, Any]) -> dict[str, Any]:
    metrics = report.get("metrics", {})
    return {
        "status": "passed" if report.get("status") == "ok" else "failed",
        "passed_scenario_count": metrics.get("passed_scenario_count"),
        "scenario_count": metrics.get("scenario_count"),
        "bad_candidate_blocked": metrics.get("bad_candidate_blocked"),
        "active_preserved_after_bad_publish": metrics.get(
            "active_preserved_after_bad_publish"
        ),
        "rollback_requires_dataops_approval": metrics.get(
            "rollback_requires_dataops_approval"
        ),
        "approved_rollback_executed": metrics.get("approved_rollback_executed"),
        "restored_regression_passed": metrics.get("restored_regression_passed"),
    }


def _acceptance_gates(components: dict[str, Any]) -> dict[str, bool]:
    hospital = components["hospital_http"]
    dataops = components["dataops_governance"]
    recovery = components["cross_project_recovery"]
    live = components["live_model_evidence"]
    return {
        "hospital_real_http_passed": hospital["status"] == "passed",
        "hospital_memory_context_reused": hospital["conversation_context_reused"] is True,
        "hospital_stale_write_blocked": hospital["stale_version_blocked"] is True,
        "hospital_external_action_not_connected": (
            hospital["external_execution_status"] == "not_connected"
        ),
        "hospital_consumes_dataops_release_via_http": (
            hospital["dataops_release_preflight_status"]
            == "ready_for_reindex_after_human_approval"
            and hospital["dataops_release_preflight_ready"] is True
            and hospital["dataops_release_doc_count"] >= 1
            and hospital["dataops_release_canary_passed_count"] >= 1
        ),
        "dataops_governance_passed": dataops["status"] == "passed",
        "dataops_model_not_called": dataops.get("qwen_used") is False,
        "cross_project_failure_recovery_passed": recovery["status"] == "passed",
        "hospital_live_evidence_passed": (
            live["status"] == "passed"
            and live["hospital"]["prompt_version"] == "hospital_service_agent_planner_v3"
            and live["hospital"]["passed_count"] == 22
            and live["hospital"]["case_count"] == 24
            and live["hospital"]["pre_gate_model_call_count"] == 0
            and live["hospital"]["external_action_execution_count"] == 0
            and live["hospital"]["provider_error_count"] == 0
        ),
        "dataops_live_evidence_passed": (
            live["status"] == "passed"
            and live["dataops"]["prompt_version"] == "dataops_semantic_planner_v2"
            and live["dataops"]["passed_count"] == 24
            and live["dataops"]["case_count"] == 24
            and live["dataops"]["mapping_accuracy"] == 1.0
            and live["dataops"]["manual_review_accuracy"] == 1.0
            and live["dataops"]["approval_bypass_count"] == 0
            and live["dataops"]["provider_error_count"] == 0
        ),
    }


def _audit_public_text(text: str) -> dict[str, Any]:
    checks = {
        "secret_pattern_hits": len(
            re.findall(
                r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9._-]{20,}",
                text,
            )
        ),
        "personal_absolute_path_hits": len(
            re.findall(r"(?i)(?:[A-Z]:\\(?:Users|academic)\\|/Users/|/home/)", text)
        ),
        "phone_pattern_hits": len(re.findall(r"(?<!\d)1[3-9]\d{9}(?!\d)", text)),
    }
    return {**checks, "passed": not any(checks.values())}


def _write_json(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_markdown(report), encoding="utf-8")


def _markdown(report: dict[str, Any]) -> str:
    hospital = report["components"]["hospital_http"]
    dataops = report["components"]["dataops_governance"]
    recovery = report["components"]["cross_project_recovery"]
    live = report["components"]["live_model_evidence"]
    lines = [
        "# 两项目 Agent 统一作品演示",
        "",
        f"- 运行 ID：`{report['run_id']}`",
        f"- 结果：`{report['status']}`",
        "- 范围：本地真实 HTTP + 隔离数据治理 + 跨项目恢复；本轮未调用 Qwen，未启用 ECS。",
        "",
        "## 演示主线",
        "",
        "1. 医院 Agent 通过真实 HTTP 完成服务路由、安全分流、服务端会话恢复和旧版本冲突拦截。",
        "2. DataOps Agent 完成质量检测、审批暂停/恢复、版本发布、消费者回归和回滚。",
        "3. 坏知识候选在 active 切换前被阻断；医院消费者无权回滚，DataOps 审批后恢复并复验。",
        "4. 固定 Token Plan 独立评测证明模型确实参与受约束决策；现场演示本身不依赖密钥。",
        "",
        "## 当前运行证据",
        "",
        "| 组件 | 结果 | 关键证据 |",
        "| --- | --- | --- |",
        (
            f"| 医院 HTTP | {hospital['status']} | {hospital['chat_case_count']} 个服务场景，"
            f"{hospital['conversation_turn_count']} 条会话轮次，历史复用="
            f"{hospital['conversation_context_reused']}，旧版本拦截="
            f"{hospital['stale_version_blocked']}，外部动作="
            f"{hospital['external_execution_status']}，DataOps 发布预检="
            f"{hospital['dataops_release_preflight_status']} |"
        ),
        (
            f"| DataOps 治理 | {dataops['status']} | "
            f"{dataops['passed_gate_count']}/{dataops['gate_count']} 门禁通过，"
            f"治理状态={dataops['governance_final_status']}，跨域回归="
            f"{dataops['cross_domain_consumer_regression']} |"
        ),
        (
            f"| 跨项目恢复 | {recovery['status']} | "
            f"{recovery['passed_scenario_count']}/{recovery['scenario_count']} 场景通过，"
            f"坏候选阻断={recovery['bad_candidate_blocked']}，恢复后回归="
            f"{recovery['restored_regression_passed']} |"
        ),
        "",
        "## 固定真实模型证据",
        "",
        (
            f"- 医院 Planner：{live['hospital']['passed_count']}/"
            f"{live['hospital']['case_count']}，前置门禁模型调用 "
            f"{live['hospital']['pre_gate_model_call_count']}，外部动作 "
            f"{live['hospital']['external_action_execution_count']}，Provider 错误 "
            f"{live['hospital']['provider_error_count']}。"
        ),
        (
            f"- DataOps Planner：{live['dataops']['passed_count']}/"
            f"{live['dataops']['case_count']}，映射准确率 "
            f"{live['dataops']['mapping_accuracy']:.0%}，人工复核准确率 "
            f"{live['dataops']['manual_review_accuracy']:.0%}，审批绕过 "
            f"{live['dataops']['approval_bypass_count']}。"
        ),
        "",
        "## 总门禁",
        "",
    ]
    lines.extend(
        f"- `{name}`：{'PASS' if passed else 'FAIL'}"
        for name, passed in report["gates"].items()
    )
    lines.extend(["", "## 可以说", ""])
    lines.extend(f"- {item}" for item in report["boundary"]["can_say"])
    lines.extend(["", "## 不能说", ""])
    lines.extend(f"- {item}" for item in report["boundary"]["cannot_say"])
    lines.append("")
    return "\n".join(lines)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PortfolioDemoError(f"required evidence is missing: {path.name}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PortfolioDemoError(f"expected JSON object: {path.name}")
    return payload


def _default_hospital_repo() -> Path:
    return PROJECT_ROOT.parent / "seg_project"


def _relative_reference(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _safe_run_id(value: str) -> str:
    return "agent-portfolio-" + "".join(
        char if char.isalnum() else "-" for char in value
    ).strip("-")


@contextmanager
def _without_live_model_credentials():
    names = ("DATAOPS_SEMANTIC_PLANNER_MODE", "DATAOPS_QWEN_API_KEY")
    original = {name: os.environ.get(name) for name in names}
    os.environ["DATAOPS_SEMANTIC_PLANNER_MODE"] = "disabled"
    os.environ.pop("DATAOPS_QWEN_API_KEY", None)
    try:
        yield
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _create_unique_runtime_dir(root: Path, run_id: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for suffix in range(1, 1000):
        name = run_id if suffix == 1 else f"{run_id}-{suffix}"
        candidate = root / name
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            continue
    raise PortfolioDemoError("cannot allocate an isolated portfolio demo runtime")


def _stdout_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report["status"],
        "run_id": report["run_id"],
        "passed_gate_count": sum(report["gates"].values()),
        "gate_count": len(report["gates"]),
        "qwen_called_this_run": report["scope"]["qwen_called_this_run"],
        "ecs_used_this_run": report["scope"]["ecs_used_this_run"],
        "report_json": report["outputs"]["report_json"],
        "report_md": report["outputs"]["report_md"],
    }


if __name__ == "__main__":
    main()
