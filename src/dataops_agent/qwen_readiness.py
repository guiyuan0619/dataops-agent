from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dataops_agent.agent_tools import validate_tool_calls
from dataops_agent.qwen_client import (
    DEFAULT_QWEN_API_KEY_ENV,
    DEFAULT_QWEN_BASE_URL,
    DEFAULT_QWEN_MODEL,
    QwenOpenAICompatibleClient,
    QwenProviderError,
    qwen_readonly_tools,
)


def run_qwen_readiness(
    *,
    report_path: str | Path,
    model: str = DEFAULT_QWEN_MODEL,
    base_url: str = DEFAULT_QWEN_BASE_URL,
    api_key_env: str = DEFAULT_QWEN_API_KEY_ENV,
    max_tokens: int = 512,
) -> dict[str, Any]:
    client = QwenOpenAICompatibleClient(
        model=model,
        base_url=base_url,
        api_key_env=api_key_env,
        timeout_seconds=30,
        max_retries=1,
    )
    cases = [
        _missing_key_degradation(model=model, base_url=base_url),
        _chat_health(client, max_tokens=min(max_tokens, 128)),
        _tool_calling_health(client, max_tokens=min(max_tokens, 256)),
    ]
    payload = _payload(cases, model=model, base_url=base_url, api_key_env=api_key_env)
    report = Path(report_path)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--model", default=DEFAULT_QWEN_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_QWEN_BASE_URL)
    parser.add_argument("--api-key-env", default=DEFAULT_QWEN_API_KEY_ENV)
    parser.add_argument("--max-tokens", type=int, default=512)
    args = parser.parse_args()
    payload = run_qwen_readiness(
        report_path=args.report,
        model=args.model,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        max_tokens=args.max_tokens,
    )
    print(json.dumps(payload["headline_metrics"], ensure_ascii=False, sort_keys=True))


def _missing_key_degradation(*, model: str, base_url: str) -> dict[str, Any]:
    started = time.perf_counter()
    passed = False
    error_type = ""
    try:
        QwenOpenAICompatibleClient(
            model=model,
            base_url=base_url,
            api_key_env="DATAOPS_QWEN_API_KEY_MISSING_FOR_TEST",
        ).chat(messages=[{"role": "user", "content": "ping"}], max_tokens=16)
    except QwenProviderError as exc:
        error_type = exc.__class__.__name__
        passed = "missing API key env" in str(exc)
    return _case(
        "QWEN-000",
        "missing_key_degradation",
        passed,
        started,
        error_type=error_type,
    )


def _chat_health(client: QwenOpenAICompatibleClient, *, max_tokens: int) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = client.chat(
            messages=[
                {
                    "role": "system",
                    "content": "你是 DataOps Agent readiness 检查器，只返回一句中文。",
                },
                {"role": "user", "content": "返回：readiness ok"},
            ],
            max_tokens=max_tokens,
        )
        return _case(
            "QWEN-001",
            "chat_health",
            bool(response.content.strip()),
            started,
            latency_ms=response.latency_ms,
            attempts=response.attempts,
            usage=response.usage,
        )
    except QwenProviderError as exc:
        return _case(
            "QWEN-001",
            "chat_health",
            False,
            started,
            error_type=exc.__class__.__name__,
            provider_status_code=exc.status_code,
            provider_error_code=exc.error_code,
        )


def _tool_calling_health(client: QwenOpenAICompatibleClient, *, max_tokens: int) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = client.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是 DataOps Agent 工具规划器。只能使用提供的工具，"
                        "不能编造 Shell、文件写入或发布工具。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "数据集 doctors 有缺失字段、重复 doctor_id、非法枚举和过期 active 记录。"
                        "请选择下一步只读治理工具。"
                    ),
                },
            ],
            tools=qwen_readonly_tools(),
            tool_choice="auto",
            max_tokens=max_tokens,
        )
        names = []
        for tool_call in response.tool_calls:
            function = tool_call.get("function") if isinstance(tool_call, dict) else {}
            names.append({"name": function.get("name") or ""})
        validate_tool_calls(names)
        return _case(
            "QWEN-002",
            "function_calling_allowlist",
            bool(names),
            started,
            latency_ms=response.latency_ms,
            attempts=response.attempts,
            usage=response.usage,
            tool_names=[item["name"] for item in names],
        )
    except (QwenProviderError, ValueError) as exc:
        status_code = exc.status_code if isinstance(exc, QwenProviderError) else None
        error_code = exc.error_code if isinstance(exc, QwenProviderError) else ""
        return _case(
            "QWEN-002",
            "function_calling_allowlist",
            False,
            started,
            error_type=exc.__class__.__name__,
            provider_status_code=status_code,
            provider_error_code=error_code,
        )


def _case(
    case_id: str,
    case_type: str,
    passed: bool,
    started: float,
    *,
    latency_ms: float | None = None,
    attempts: int = 0,
    usage: dict[str, Any] | None = None,
    error_type: str = "",
    provider_status_code: int | None = None,
    provider_error_code: str = "",
    tool_names: list[str] | None = None,
) -> dict[str, Any]:
    active_usage = usage or {}
    return {
        "case_id": case_id,
        "case_type": case_type,
        "passed": passed,
        "latency_ms": latency_ms
        if latency_ms is not None
        else round((time.perf_counter() - started) * 1000, 3),
        "attempts": attempts,
        "prompt_tokens": int(active_usage.get("prompt_tokens") or 0),
        "completion_tokens": int(active_usage.get("completion_tokens") or 0),
        "total_tokens": int(active_usage.get("total_tokens") or 0),
        "tool_names": tool_names or [],
        "error_type": error_type,
        "provider_status_code": provider_status_code,
        "provider_error_code": provider_error_code,
    }


def _payload(
    cases: list[dict[str, Any]],
    *,
    model: str,
    base_url: str,
    api_key_env: str,
) -> dict[str, Any]:
    passed = sum(1 for case in cases if case["passed"])
    return {
        "status": "completed" if passed == len(cases) else "failed",
        "generated_at": datetime.now(UTC).isoformat(),
        "model": model,
        "base_url": base_url,
        "api_key_env": api_key_env,
        "api_key_recorded": False,
        "headline_metrics": {
            "case_count": len(cases),
            "passed_count": passed,
            "overall_pass_rate": round(passed / len(cases), 4),
            "live_request_count": 2,
            "total_tokens": sum(int(case["total_tokens"]) for case in cases),
            "function_calling_valid": any(
                case["case_type"] == "function_calling_allowlist" and case["passed"]
                for case in cases
            ),
        },
        "cases": cases,
        "boundary": [
            "Key was read only from the current process environment.",
            "The report stores env var name and metrics only, never the key value.",
            "This is a readiness slice, not batch cleaning or production traffic.",
            "Write tools remain behind deterministic approval and are not exposed to Qwen live.",
        ],
    }


if __name__ == "__main__":
    main()
