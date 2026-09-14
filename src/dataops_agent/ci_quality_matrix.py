from __future__ import annotations

import argparse
import json
import re
import tomllib
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "dataops_ci_quality_matrix.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "dataops_ci_quality_matrix.md"
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "dataops-quality-gate.yml"
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
TEST_ROOT = PROJECT_ROOT / "tests"
JUNIT_XML = PROJECT_ROOT / "reports" / "pytest_junit.xml"
COVERAGE_XML = PROJECT_ROOT / "reports" / "pytest_coverage.xml"

TEST_MATRIX = [
    {
        "suite": "ingestion-contracts",
        "focus": "来源、数据合同、候选数据验收",
        "files": [
            "tests/test_source_manifest.py",
            "tests/test_sources.py",
            "tests/test_service_requests.py",
            "tests/test_cms_hospitals.py",
            "tests/test_private_sources.py",
            "tests/test_contracts.py",
            "tests/test_acceptance.py",
        ],
    },
    {
        "suite": "governance-release",
        "focus": "治理状态、审批发布、回归门禁、知识发布",
        "files": [
            "tests/test_governance.py",
            "tests/test_semantic_agent_mainline.py",
            "tests/test_semantic_planner_p0_eval.py",
            "tests/test_decision_memory.py",
            "tests/test_api.py",
            "tests/test_rules_repair_versioning.py",
            "tests/test_concurrency.py",
            "tests/test_approval_evidence.py",
            "tests/test_cross_project_recovery.py",
            "tests/test_downstream_regression.py",
            "tests/test_consumer_integration.py",
            "tests/test_knowledge_release.py",
            "tests/test_integration_audit.py",
        ],
    },
    {
        "suite": "agent-tooling",
        "focus": "MCP、Skill、Qwen 边界、Reviewer、scorecard",
        "files": [
            "tests/test_mcp_server.py",
            "tests/test_mcp_showcase.py",
            "tests/test_skill_registry.py",
            "tests/test_qwen_client.py",
            "tests/test_release_review.py",
            "tests/test_scorecard.py",
            "tests/test_training_candidates.py",
            "tests/test_ci_quality_matrix.py",
        ],
    },
    {
        "suite": "safety-collaboration",
        "focus": "安全对抗、能力边界、协作契约、场景迁移",
        "files": [
            "tests/test_security_review.py",
            "tests/test_boundary_guard.py",
            "tests/test_collaboration.py",
            "tests/test_scenario_playbook.py",
        ],
    },
    {
        "suite": "downstream-observability",
        "focus": "新鲜度、性能、demo、医院下游影响",
        "files": [
            "tests/test_freshness.py",
            "tests/test_rolling_open_data.py",
            "tests/test_performance.py",
            "tests/test_demo.py",
            "tests/test_portfolio_demo.py",
            "tests/test_hospital_integration.py",
            "tests/test_hospital_regression.py",
            "tests/test_hospital_impact.py",
        ],
    },
]

EXPECTED_MARKERS = ["unit", "api", "integration", "ai_eval", "security", "performance", "slow"]


def run_ci_quality_matrix(
    *,
    report_json: Path | str = DEFAULT_REPORT_JSON,
    report_md: Path | str = DEFAULT_REPORT_MD,
    run_id: str | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    run_id = run_id or _safe_run_id(generated_at)

    actual_test_files = sorted(
        _relative(path) for path in TEST_ROOT.glob("test_*.py") if path.is_file()
    )
    matrix_files = [file for suite in TEST_MATRIX for file in suite["files"]]
    duplicate_files = sorted(file for file in set(matrix_files) if matrix_files.count(file) > 1)
    missing_from_matrix = sorted(set(actual_test_files) - set(matrix_files))
    stale_matrix_files = sorted(set(matrix_files) - set(actual_test_files))
    declared_markers = _declared_markers()
    used_markers = _used_markers(actual_test_files)
    workflow = _workflow_summary(WORKFLOW_PATH)
    workflow_missing_matrix_files = sorted(set(matrix_files) - set(workflow["matrix_test_files"]))
    junit = _junit_summary(JUNIT_XML)
    coverage = _coverage_summary(COVERAGE_XML)

    suites = []
    for suite in TEST_MATRIX:
        files = suite["files"]
        suites.append(
            {
                "suite": suite["suite"],
                "focus": suite["focus"],
                "file_count": len(files),
                "files": files,
                "command": "python -m pytest " + " ".join(files),
            }
        )

    gates = {
        "workflow_file_present": workflow["report_found"],
        "workflow_runs_ruff": workflow["runs_ruff"],
        "workflow_runs_test_matrix": workflow["runs_test_matrix"],
        "workflow_runs_full_coverage": workflow["runs_full_coverage"],
        "workflow_runs_scorecard": workflow["runs_scorecard"],
        "workflow_runs_ci_matrix_report": workflow["runs_ci_matrix_report"],
        "workflow_covers_test_matrix": not workflow_missing_matrix_files,
        "all_test_files_assigned": not missing_from_matrix,
        "no_stale_matrix_files": not stale_matrix_files,
        "no_duplicate_test_assignment": not duplicate_files,
        "expected_markers_declared": set(EXPECTED_MARKERS).issubset(set(declared_markers)),
        "junit_snapshot_available": junit["report_found"],
        "coverage_snapshot_available": coverage["report_found"],
    }
    required_gate_names = [
        "workflow_file_present",
        "workflow_runs_ruff",
        "workflow_runs_test_matrix",
        "workflow_runs_full_coverage",
        "workflow_runs_scorecard",
        "workflow_runs_ci_matrix_report",
        "workflow_covers_test_matrix",
        "all_test_files_assigned",
        "no_stale_matrix_files",
        "no_duplicate_test_assignment",
        "expected_markers_declared",
    ]

    report = {
        "status": "ok" if all(gates[name] for name in required_gate_names) else "failed",
        "run_id": run_id,
        "generated_at": generated_at,
        "scope": {
            "local_only": True,
            "ci_workflow_configured": workflow["report_found"],
            "ci_provider_executed": False,
            "trend_history_available": False,
            "ecs_used_this_run": False,
            "qwen_called_this_run": False,
            "production_quality_platform": False,
        },
        "outputs": {
            "report_json": str(Path(report_json)),
            "report_md": str(Path(report_md)),
        },
        "suites": suites,
        "markers": {
            "declared": declared_markers,
            "used": used_markers,
            "expected": EXPECTED_MARKERS,
        },
        "snapshots": {
            "junit": junit,
            "coverage": coverage,
        },
        "workflow": workflow,
        "gates": gates,
        "required_gates": required_gate_names,
        "metrics": {
            "suite_count": len(TEST_MATRIX),
            "test_file_count": len(actual_test_files),
            "assigned_test_file_count": len(set(matrix_files) & set(actual_test_files)),
            "missing_from_matrix_count": len(missing_from_matrix),
            "stale_matrix_file_count": len(stale_matrix_files),
            "duplicate_assignment_count": len(duplicate_files),
            "declared_marker_count": len(declared_markers),
            "used_marker_count": len(used_markers),
            "junit_test_count": junit.get("tests"),
            "junit_failure_count": junit.get("failures"),
            "junit_error_count": junit.get("errors"),
            "coverage_line_rate": coverage.get("line_rate"),
            "coverage_branch_rate": coverage.get("branch_rate"),
        },
        "matrix_drift": {
            "missing_from_matrix": missing_from_matrix,
            "stale_matrix_files": stale_matrix_files,
            "duplicate_files": duplicate_files,
            "workflow_missing_matrix_files": workflow_missing_matrix_files,
        },
        "resume_boundary": {
            "can_say": [
                "项目具备 CI-ready 质量门禁配置和本地测试矩阵证据。",
                (
                    "测试矩阵覆盖全部 test_*.py 文件，并按数据接入、治理发布、"
                    "Agent 工具、安全协作、下游可观测五类组织。"
                ),
                "当前已有 pytest、coverage XML、JUnit 和 ruff 质量门禁证据。",
            ],
            "cannot_say": [
                "不能说 GitHub Actions 或企业 CI 已经真实运行通过，除非拿到远端运行记录。",
                "不能说已经有 Allure、质量平台、生产压测趋势或生产缺陷看板。",
                "不能把本地 coverage/JUnit 报告包装成生产质量 SLA。",
            ],
        },
    }
    _write_json(report_json, report)
    _write_markdown(report_md, report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate DataOps CI quality matrix evidence.")
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    report = run_ci_quality_matrix(
        report_json=args.report_json,
        report_md=args.report_md,
        run_id=args.run_id,
    )
    print(json.dumps(_stdout_summary(report), ensure_ascii=False, indent=2))
    if report["status"] != "ok":
        raise SystemExit(1)


def _declared_markers() -> list[str]:
    with PYPROJECT.open("rb") as handle:
        pyproject = tomllib.load(handle)
    marker_defs = pyproject["tool"]["pytest"]["ini_options"]["markers"]
    return sorted(marker.split(":", 1)[0].strip() for marker in marker_defs)


def _used_markers(test_files: list[str]) -> list[str]:
    pattern = re.compile(r"pytest\.mark\.([A-Za-z_][A-Za-z0-9_]*)")
    markers: set[str] = set()
    for relative_path in test_files:
        text = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        markers.update(pattern.findall(text))
    return sorted(markers)


def _workflow_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "report_found": False,
            "path": str(path),
            "runs_ruff": False,
            "runs_test_matrix": False,
            "runs_full_coverage": False,
            "runs_scorecard": False,
            "runs_ci_matrix_report": False,
            "matrix_test_files": [],
        }
    text = path.read_text(encoding="utf-8")
    matrix_test_files = sorted(set(re.findall(r"tests/test_[\w]+\.py", text)))
    return {
        "report_found": True,
        "path": str(path),
        "runs_ruff": "ruff check" in text,
        "runs_test_matrix": (
            "test-matrix" in text and "python -m pytest ${{ matrix.files }}" in text
        ),
        "runs_full_coverage": (
            "--cov=dataops_agent" in text and "--junitxml=reports/pytest_junit.xml" in text
        ),
        "runs_scorecard": "python -m dataops_agent.scorecard" in text,
        "runs_ci_matrix_report": "python -m dataops_agent.ci_quality_matrix" in text,
        "matrix_test_files": matrix_test_files,
        "contains_secret_context": "secrets." in text or "QWEN_API_KEY" in text or "ssh " in text,
    }


def _junit_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"report_found": False, "path": str(path)}
    root = ET.parse(path).getroot()
    return {
        "report_found": True,
        "path": str(path),
        "tests": _xml_total(root, "tests"),
        "failures": _xml_total(root, "failures"),
        "errors": _xml_total(root, "errors"),
        "skipped": _xml_total(root, "skipped"),
    }


def _coverage_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"report_found": False, "path": str(path)}
    root = ET.parse(path).getroot()
    line_rate = float(root.attrib.get("line-rate", 0.0))
    branch_rate = float(root.attrib.get("branch-rate", 0.0))
    return {
        "report_found": True,
        "path": str(path),
        "line_rate": round(line_rate, 4),
        "branch_rate": round(branch_rate, 4),
        "line_rate_percent": round(line_rate * 100, 2),
        "branch_rate_percent": round(branch_rate * 100, 2),
        "lines_valid": _xml_int(root, "lines-valid"),
        "lines_covered": _xml_int(root, "lines-covered"),
        "branches_valid": _xml_int(root, "branches-valid"),
        "branches_covered": _xml_int(root, "branches-covered"),
    }


def _xml_int(root: ET.Element, key: str) -> int:
    return int(float(root.attrib.get(key, 0)))


def _xml_total(root: ET.Element, key: str) -> int:
    if key in root.attrib:
        return _xml_int(root, key)
    return sum(_xml_int(child, key) for child in root.findall("testsuite"))


def _write_json(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_markdown(path: Path | str, report: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    metrics = report["metrics"]
    coverage = report["snapshots"]["coverage"]
    junit = report["snapshots"]["junit"]
    lines = [
        "# DataOps CI 质量门禁与测试矩阵",
        "",
        f"- 运行 ID：`{report['run_id']}`",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 结果：`{report['status']}`",
        "- 范围：本地生成的 CI-ready 证据；未调用 Qwen，未启用 ECS，未执行远端 CI。",
        "",
        "## 测试矩阵",
        "",
        "| suite | 关注点 | 文件数 | 命令 |",
        "|---|---|---:|---|",
    ]
    for suite in report["suites"]:
        lines.append(
            f"| `{suite['suite']}` | {suite['focus']} | {suite['file_count']} | "
            f"`{suite['command']}` |"
        )
    lines.extend(
        [
            "",
            "## 门禁结果",
            "",
            "| 门禁 | 结果 |",
            "|---|---|",
        ]
    )
    for name, value in report["gates"].items():
        lines.append(f"| `{name}` | `{value}` |")
    lines.extend(
        [
            "",
            "## 当前报告快照",
            "",
            (
                f"- 测试文件：{metrics['assigned_test_file_count']}/"
                f"{metrics['test_file_count']} 已进入矩阵。"
            ),
            (
                "- JUnit："
                f"found={junit['report_found']}，tests={junit.get('tests')}，"
                f"failures={junit.get('failures')}，errors={junit.get('errors')}。"
            ),
            (
                "- Coverage："
                f"found={coverage['report_found']}，line={coverage.get('line_rate_percent')}%，"
                f"branch={coverage.get('branch_rate_percent')}%。"
            ),
            "",
            "## 面试口径",
            "",
            "- 可以说：项目具备 CI-ready 质量门禁配置和本地测试矩阵证据。",
            "- 可以说：测试矩阵覆盖全部 test_*.py 文件，并按五类风险链路组织。",
            "- 不能说：远端 GitHub Actions 或企业 CI 已经真实运行通过。",
            "- 不能说：已经有 Allure、质量平台、生产压测趋势或生产缺陷看板。",
            "",
        ]
    )
    destination.write_text("\n".join(lines), encoding="utf-8")


def _relative(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def _safe_run_id(value: str) -> str:
    return "ci-quality-" + "".join(char if char.isalnum() else "-" for char in value).strip("-")


def _stdout_summary(report: dict[str, Any]) -> dict[str, Any]:
    metrics = report["metrics"]
    return {
        "status": report["status"],
        "run_id": report["run_id"],
        "suite_count": metrics["suite_count"],
        "test_file_count": metrics["test_file_count"],
        "assigned_test_file_count": metrics["assigned_test_file_count"],
        "missing_from_matrix_count": metrics["missing_from_matrix_count"],
        "workflow_file_present": report["gates"]["workflow_file_present"],
        "junit_tests": metrics["junit_test_count"],
        "coverage_line_rate": metrics["coverage_line_rate"],
        "coverage_branch_rate": metrics["coverage_branch_rate"],
        "ci_provider_executed": report["scope"]["ci_provider_executed"],
        "qwen_called_this_run": report["scope"]["qwen_called_this_run"],
        "ecs_used_this_run": report["scope"]["ecs_used_this_run"],
        "report_json": report["outputs"]["report_json"],
        "report_md": report["outputs"]["report_md"],
    }


if __name__ == "__main__":
    main()
