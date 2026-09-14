from __future__ import annotations

import argparse
import importlib
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from dataops_agent.rules import load_csv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEG_PROJECT_ROOT = PROJECT_ROOT.parent / (
    "hospital-service-agent"
    if (PROJECT_ROOT.parent / "hospital-service-agent").is_dir()
    else "seg_project"
)
DEFAULT_KNOWLEDGE_OUTPUT = PROJECT_ROOT / "reports" / "hospital_downstream_knowledge.jsonl"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "hospital_downstream_regression.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "hospital_downstream_regression.md"


@dataclass(frozen=True)
class RetrievalRegressionCase:
    case_id: str
    query: str
    expected_doc_id: str | None = None
    expected_tool_name: str | None = None
    expected_terms: tuple[str, ...] = ()
    forbidden_doc_id: str | None = None


@dataclass(frozen=True)
class LocalSearchHit:
    doc_id: str
    title: str
    content: str
    keywords: list[str]
    tool_name: str
    score: float


@dataclass(frozen=True)
class LocalSearchResult:
    hits: list[LocalSearchHit]
    effective_retrieval_mode: str


class HospitalSearchService(Protocol):
    def search_with_mode(
        self,
        query: str,
        *,
        top_k: int,
        service_type: str | None = None,
        retrieval_mode: str = "bm25",
    ) -> Any: ...


def run_hospital_downstream_regression(
    *,
    dataset_path: Path | str,
    dataset_name: str = "doctors",
    version_id: str = "candidate",
    knowledge_output: Path | str = DEFAULT_KNOWLEDGE_OUTPUT,
    report_json: Path | str | None = DEFAULT_REPORT_JSON,
    report_md: Path | str | None = DEFAULT_REPORT_MD,
    seg_project_root: Path | str = DEFAULT_SEG_PROJECT_ROOT,
    case_limit: int = 5,
    top_k: int = 3,
) -> dict[str, Any]:
    df = load_csv(str(dataset_path))
    knowledge_path = Path(knowledge_output)
    docs = build_hospital_knowledge_docs(df, dataset_name=dataset_name, version_id=version_id)
    write_hospital_knowledge_jsonl(docs, knowledge_path)
    cases = build_retrieval_cases(df, dataset_name=dataset_name, case_limit=case_limit)
    service = _hospital_search_service(Path(seg_project_root), knowledge_path)
    evaluation = evaluate_hospital_retrieval_cases(service, cases, top_k=top_k)
    report = {
        "status": "passed" if evaluation["passed"] else "failed",
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "dataset_name": dataset_name,
        "dataset_path": str(dataset_path),
        "version_id": version_id,
        "knowledge_output": str(knowledge_path),
        "outputs": {
            "knowledge_jsonl": str(knowledge_path),
            "report_json": str(report_json) if report_json is not None else None,
            "report_md": str(report_md) if report_md is not None else None,
        },
        "seg_project_root": str(seg_project_root),
        "search_provider": service.__class__.__name__,
        "scope": {
            "local_only": True,
            "ecs_used": False,
            "qwen_used": False,
            "retrieval_mode": "bm25",
            "production_traffic": False,
        },
        "knowledge_doc_count": len(docs),
        "case_count": len(cases),
        "release_gate_passed": evaluation["passed"],
        "metrics": evaluation["metrics"],
        "cases": evaluation["cases"],
        "interview_boundary": {
            "can_say": [
                "DataOps approved version can be converted into Hospital Agent knowledge JSONL.",
                "Hospital Agent local BM25 retrieval hits the expected released doctor records.",
                "Inactive doctor records are not exported and do not appear in top-k retrieval.",
            ],
            "cannot_say": [
                "This is production online QA accuracy.",
                "ECS public traffic was used.",
                "Qwen generated or repaired answers in this regression.",
            ],
        },
    }
    if report_json is not None:
        _write_json(Path(report_json), report)
    if report_md is not None:
        _write_markdown(Path(report_md), report)
    return report


def build_hospital_knowledge_docs(
    df: pd.DataFrame,
    *,
    dataset_name: str,
    version_id: str,
) -> list[dict[str, Any]]:
    if dataset_name == "hospitals":
        return _hospital_docs(df, version_id=version_id)
    return _doctor_docs(df, version_id=version_id)


def write_hospital_knowledge_jsonl(docs: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(doc, ensure_ascii=False, sort_keys=True) for doc in docs)
    path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")


def build_retrieval_cases(
    df: pd.DataFrame,
    *,
    dataset_name: str,
    case_limit: int = 5,
) -> list[RetrievalRegressionCase]:
    if dataset_name == "hospitals":
        return _hospital_cases(df, case_limit=case_limit)
    return _doctor_cases(df, case_limit=case_limit)


def evaluate_hospital_retrieval_cases(
    service: HospitalSearchService,
    cases: list[RetrievalRegressionCase],
    *,
    top_k: int = 3,
) -> dict[str, Any]:
    results = [_run_case(service, case, top_k=top_k) for case in cases]
    positive = [case for case in results if case["expected_doc_id"]]
    negative = [case for case in results if case["forbidden_doc_id"]]
    metrics = {
        "positive_case_count": len(positive),
        "negative_case_count": len(negative),
        "passed_case_count": sum(1 for case in results if case["passed"]),
        "pass_rate": _ratio(sum(1 for case in results if case["passed"]), len(results)),
        "top1_accuracy": _ratio(sum(1 for case in positive if case["top1_hit"]), len(positive)),
        "hit_at_k": _ratio(sum(1 for case in positive if case["hit_at_k"]), len(positive)),
        "tool_match_rate": _ratio(sum(1 for case in positive if case["tool_match"]), len(positive)),
        "term_coverage_rate": _ratio(
            sum(1 for case in positive if case["term_coverage"]), len(positive)
        ),
        "forbidden_doc_leak_count": sum(1 for case in negative if case["forbidden_doc_leaked"]),
    }
    return {
        "passed": bool(results)
        and metrics["passed_case_count"] == len(results)
        and metrics["forbidden_doc_leak_count"] == 0,
        "metrics": metrics,
        "cases": results,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run DataOps -> Hospital Agent retrieval regression locally."
    )
    parser.add_argument(
        "--dataset-path", default=str(PROJECT_ROOT / "data" / "raw" / "clean_dev.csv")
    )
    parser.add_argument("--dataset-name", default="doctors")
    parser.add_argument("--version-id", default="candidate")
    parser.add_argument("--knowledge-output", default=str(DEFAULT_KNOWLEDGE_OUTPUT))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--seg-project-root", default=str(DEFAULT_SEG_PROJECT_ROOT))
    parser.add_argument("--case-limit", type=int, default=5)
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args(argv)
    report = run_hospital_downstream_regression(
        dataset_path=args.dataset_path,
        dataset_name=args.dataset_name,
        version_id=args.version_id,
        knowledge_output=args.knowledge_output,
        report_json=args.report_json,
        report_md=args.report_md,
        seg_project_root=args.seg_project_root,
        case_limit=args.case_limit,
        top_k=args.top_k,
    )
    print(json.dumps(_stdout_summary(report), ensure_ascii=False, indent=2))
    if report["status"] != "passed":
        raise SystemExit(1)


def _doctor_docs(df: pd.DataFrame, *, version_id: str) -> list[dict[str, Any]]:
    rows = df.fillna("")
    docs: list[dict[str, Any]] = []
    for index, row in rows.iterrows():
        doctor_id = _value(row, "doctor_id")
        status = _value(row, "status")
        if not doctor_id or status != "active":
            continue
        name = _value(row, "name")
        department = _value(row, "department")
        title = _value(row, "title")
        updated_at = _value(row, "updated_at")
        docs.append(
            {
                "campus": "all",
                "content": (
                    f"{name} is an active {title} in {department}. "
                    "Use the doctor schedule tool to verify appointments and availability."
                ),
                "doc_id": f"DATAOPS-DOCTOR-{doctor_id}",
                "keywords": _keywords(
                    doctor_id,
                    name,
                    department,
                    title,
                    status,
                    "doctor",
                    "schedule",
                    "appointment",
                    "availability",
                ),
                "service_type": "doctor_schedule",
                "source_checked_at": updated_at,
                "source_key": "dataops_doctors",
                "source_lines": f"row:{int(index) + 2}",
                "source_title": "DataOps approved doctors version",
                "source_type": "dataops_release",
                "source_url": f"dataops://versions/{version_id}/doctors/{doctor_id}",
                "title": f"{name} doctor schedule record",
                "tool_name": "doctor_schedule_tool",
            }
        )
    return docs


def _hospital_docs(df: pd.DataFrame, *, version_id: str) -> list[dict[str, Any]]:
    rows = df.fillna("")
    docs: list[dict[str, Any]] = []
    for index, row in rows.iterrows():
        hospital_id = _value(row, "hospital_id")
        name = _value(row, "name")
        if not hospital_id or not name:
            continue
        city = _value(row, "city")
        state = _value(row, "state")
        hospital_type = _value(row, "hospital_type")
        emergency = _value(row, "emergency_services")
        rating = _value(row, "rating")
        source_url = _value(row, "source_url")
        docs.append(
            {
                "campus": f"{city}, {state}".strip(", "),
                "content": (
                    f"{name} is a {hospital_type} in {city}, {state}. "
                    f"Emergency services: {emergency}. Rating: {rating}."
                ),
                "doc_id": f"DATAOPS-HOSPITAL-{hospital_id}",
                "keywords": _keywords(
                    hospital_id,
                    name,
                    city,
                    state,
                    hospital_type,
                    emergency,
                    rating,
                    "hospital",
                    "profile",
                ),
                "service_type": "navigation",
                "source_checked_at": "",
                "source_key": "dataops_hospitals",
                "source_lines": f"row:{int(index) + 2}",
                "source_title": "DataOps approved hospitals version",
                "source_type": "dataops_release",
                "source_url": source_url
                or f"dataops://versions/{version_id}/hospitals/{hospital_id}",
                "title": f"{name} hospital profile",
                "tool_name": "campus_navigation_tool",
            }
        )
    return docs


def _doctor_cases(df: pd.DataFrame, *, case_limit: int) -> list[RetrievalRegressionCase]:
    rows = df.fillna("")
    cases: list[RetrievalRegressionCase] = []
    active = rows[rows["status"].astype(str).str.strip() == "active"] if "status" in rows else rows
    for _, row in active.head(case_limit).iterrows():
        doctor_id = _value(row, "doctor_id")
        name = _value(row, "name")
        department = _value(row, "department")
        if not doctor_id or not name:
            continue
        cases.append(
            RetrievalRegressionCase(
                case_id=f"doctor-{doctor_id}",
                query=f"{name} {department} doctor schedule",
                expected_doc_id=f"DATAOPS-DOCTOR-{doctor_id}",
                expected_tool_name="doctor_schedule_tool",
                expected_terms=(name, department),
            )
        )
    if "status" in rows:
        inactive = rows[rows["status"].astype(str).str.strip() != "active"]
        if not inactive.empty:
            row = inactive.iloc[0]
            doctor_id = _value(row, "doctor_id")
            name = _value(row, "name")
            department = _value(row, "department")
            if doctor_id and name:
                cases.append(
                    RetrievalRegressionCase(
                        case_id=f"inactive-doctor-{doctor_id}",
                        query=f"{name} {department} doctor schedule",
                        forbidden_doc_id=f"DATAOPS-DOCTOR-{doctor_id}",
                    )
                )
    return cases


def _hospital_cases(df: pd.DataFrame, *, case_limit: int) -> list[RetrievalRegressionCase]:
    rows = df.fillna("")
    cases: list[RetrievalRegressionCase] = []
    for _, row in rows.head(case_limit).iterrows():
        hospital_id = _value(row, "hospital_id")
        name = _value(row, "name")
        city = _value(row, "city")
        if not hospital_id or not name:
            continue
        cases.append(
            RetrievalRegressionCase(
                case_id=f"hospital-{hospital_id}",
                query=f"{name} {city} hospital profile",
                expected_doc_id=f"DATAOPS-HOSPITAL-{hospital_id}",
                expected_tool_name="campus_navigation_tool",
                expected_terms=(name, city),
            )
        )
    return cases


def _run_case(
    service: HospitalSearchService,
    case: RetrievalRegressionCase,
    *,
    top_k: int,
) -> dict[str, Any]:
    service_type = "doctor_schedule" if case.expected_tool_name == "doctor_schedule_tool" else None
    result = service.search_with_mode(
        case.query,
        top_k=top_k,
        service_type=service_type,
        retrieval_mode="bm25",
    )
    hits = [asdict(hit) for hit in result.hits]
    hit_ids = [hit["doc_id"] for hit in hits]
    matched_hit = _find_hit(hits, case.expected_doc_id)
    top1_hit = bool(case.expected_doc_id and hit_ids[:1] == [case.expected_doc_id])
    hit_at_k = bool(case.expected_doc_id and case.expected_doc_id in hit_ids)
    tool_match = bool(
        matched_hit
        and case.expected_tool_name
        and matched_hit["tool_name"] == case.expected_tool_name
    )
    term_coverage = bool(matched_hit and _terms_present(matched_hit, case.expected_terms))
    forbidden_doc_leaked = bool(case.forbidden_doc_id and case.forbidden_doc_id in hit_ids)
    failures = []
    if case.expected_doc_id and not hit_at_k:
        failures.append("expected_doc_not_in_top_k")
    if case.expected_tool_name and not tool_match:
        failures.append("expected_tool_mismatch")
    if case.expected_terms and not term_coverage:
        failures.append("expected_terms_missing")
    if forbidden_doc_leaked:
        failures.append("forbidden_doc_leaked")
    return {
        **asdict(case),
        "passed": not failures,
        "failures": failures,
        "top1_hit": top1_hit,
        "hit_at_k": hit_at_k,
        "tool_match": tool_match,
        "term_coverage": term_coverage,
        "forbidden_doc_leaked": forbidden_doc_leaked,
        "effective_retrieval_mode": result.effective_retrieval_mode,
        "top_doc_id": hit_ids[0] if hit_ids else None,
        "top_tool_name": hits[0]["tool_name"] if hits else None,
        "hit_doc_ids": hit_ids,
        "hits": hits[:top_k],
    }


def _hospital_search_service(seg_project_root: Path, knowledge_path: Path) -> HospitalSearchService:
    if not seg_project_root.exists():
        return _LocalHospitalSearchService(knowledge_path)
    root = str(seg_project_root)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        module = importlib.import_module("src.backend.services.hospital_rag_service")
    except Exception:  # pragma: no cover - depends on sibling project environment
        return _LocalHospitalSearchService(knowledge_path)
    return module.HospitalKnowledgeSearchService(data_path=knowledge_path)


class _LocalHospitalSearchService:
    def __init__(self, data_path: Path) -> None:
        payload = data_path.read_text(encoding="utf-8").splitlines()
        self._docs = [json.loads(line) for line in payload if line.strip()]

    def search_with_mode(
        self,
        query: str,
        *,
        top_k: int,
        service_type: str | None = None,
        retrieval_mode: str = "bm25",
    ) -> LocalSearchResult:
        tokens = _query_tokens(query)
        scored = []
        for doc in self._docs:
            if service_type and doc.get("service_type") != service_type:
                continue
            score = _score_doc(doc, tokens)
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda item: (-item[0], str(item[1].get("doc_id", ""))))
        hits = [
            LocalSearchHit(
                doc_id=str(doc.get("doc_id", "")),
                title=str(doc.get("title", "")),
                content=str(doc.get("content", "")),
                keywords=[str(item) for item in doc.get("keywords", [])],
                tool_name=str(doc.get("tool_name", "")),
                score=score,
            )
            for score, doc in scored[:top_k]
        ]
        return LocalSearchResult(hits=hits, effective_retrieval_mode=retrieval_mode)


def _find_hit(hits: list[dict[str, Any]], doc_id: str | None) -> dict[str, Any] | None:
    if not doc_id:
        return None
    for hit in hits:
        if hit["doc_id"] == doc_id:
            return hit
    return None


def _terms_present(hit: dict[str, Any], terms: tuple[str, ...]) -> bool:
    text = " ".join(
        [
            str(hit.get("title", "")),
            str(hit.get("content", "")),
            " ".join(str(item) for item in hit.get("keywords", [])),
        ]
    ).lower()
    return all(term.lower() in text for term in terms if term)


def _query_tokens(query: str) -> list[str]:
    return [token for token in query.lower().replace("-", " ").split() if token]


def _score_doc(doc: dict[str, Any], tokens: list[str]) -> float:
    text = " ".join(
        [
            str(doc.get("doc_id", "")),
            str(doc.get("title", "")),
            str(doc.get("content", "")),
            " ".join(str(item) for item in doc.get("keywords", [])),
        ]
    ).lower()
    return float(sum(1 for token in tokens if token in text))


def _keywords(*values: str) -> list[str]:
    seen = set()
    result = []
    for value in values:
        clean = str(value).strip()
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            result.append(clean)
    return result


def _value(row: pd.Series, field: str) -> str:
    return str(row.get(field, "")).strip()


def _ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else round(numerator / denominator, 4)


def _write_json(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_markdown_report(report), encoding="utf-8")


def _markdown_report(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# Hospital Agent 下游检索回归报告",
        "",
        f"- 数据集：`{report['dataset_name']}`",
        f"- 发布版本：`{report['version_id']}`",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 结果：`{report['status']}`",
        "- 范围：本地 BM25 检索回归，未启用 ECS，未调用 Qwen，非生产流量。",
        "",
        "## 指标",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| positive_case_count | {metrics['positive_case_count']} |",
        f"| negative_case_count | {metrics['negative_case_count']} |",
        f"| pass_rate | {metrics['pass_rate']} |",
        f"| top1_accuracy | {metrics['top1_accuracy']} |",
        f"| hit_at_k | {metrics['hit_at_k']} |",
        f"| tool_match_rate | {metrics['tool_match_rate']} |",
        f"| term_coverage_rate | {metrics['term_coverage_rate']} |",
        f"| forbidden_doc_leak_count | {metrics['forbidden_doc_leak_count']} |",
        "",
        "## 用例结果",
        "",
        "| case | query | expected | top | passed |",
        "|---|---|---|---|---|",
    ]
    for case in report["cases"]:
        expected = case["expected_doc_id"] or f"forbid {case['forbidden_doc_id']}"
        lines.append(
            "| "
            f"{case['case_id']} | "
            f"{case['query']} | "
            f"{expected} | "
            f"{case['top_doc_id']} | "
            f"{case['passed']} |"
        )
    lines.extend(
        [
            "",
            "## 面试边界",
            "",
            "- 可以说：DataOps 审批发布版本可转成医院 Agent 知识 JSONL，并通过本地检索回归。",
            "- 不能说：真实线上问答准确率、真实患者流量、ECS 公网压测或本轮 Qwen 修复。",
            "",
        ]
    )
    return "\n".join(lines)


def _stdout_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report["status"],
        "dataset_name": report["dataset_name"],
        "version_id": report["version_id"],
        "case_count": report["case_count"],
        "pass_rate": report["metrics"]["pass_rate"],
        "top1_accuracy": report["metrics"]["top1_accuracy"],
        "hit_at_k": report["metrics"]["hit_at_k"],
        "report_json": report["outputs"]["report_json"],
        "report_md": report["outputs"]["report_md"],
        "knowledge_jsonl": report["knowledge_output"],
    }


if __name__ == "__main__":
    main()
