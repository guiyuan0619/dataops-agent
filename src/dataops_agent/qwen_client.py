from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

DEFAULT_QWEN_BASE_URL = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
DEFAULT_QWEN_MODEL = "qwen3.7-plus"
DEFAULT_QWEN_API_KEY_ENV = "DATAOPS_QWEN_API_KEY"


class QwenProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


@dataclass(frozen=True)
class QwenResponse:
    content: str
    tool_calls: list[dict[str, Any]]
    usage: dict[str, Any]
    response_id: str
    model: str
    latency_ms: float
    attempts: int


class QwenOpenAICompatibleClient:
    def __init__(
        self,
        *,
        model: str = DEFAULT_QWEN_MODEL,
        base_url: str = DEFAULT_QWEN_BASE_URL,
        api_key_env: str = DEFAULT_QWEN_API_KEY_ENV,
        timeout_seconds: int = 30,
        max_retries: int = 1,
        enable_thinking: bool = False,
        urlopen: Any | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.enable_thinking = enable_thinking
        self._urlopen = urlopen

    def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> QwenResponse:
        if max_tokens <= 0:
            raise QwenProviderError("max_tokens must be positive")
        api_key = os.getenv(self.api_key_env, "").strip()
        if not api_key:
            raise QwenProviderError(f"missing API key env: {self.api_key_env}")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
            "enable_thinking": self.enable_thinking,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib_request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = time.perf_counter()
        response_body, attempts = self._post_with_retries(request)
        parsed = _parse_response(response_body)
        return QwenResponse(
            content=parsed["content"],
            tool_calls=parsed["tool_calls"],
            usage=parsed["usage"],
            response_id=parsed["response_id"],
            model=str(parsed.get("model") or self.model),
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            attempts=attempts,
        )

    def _post_with_retries(self, request: urllib_request.Request) -> tuple[bytes, int]:
        open_fn = self._urlopen or urllib_request.urlopen
        last_error: BaseException | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with open_fn(request, timeout=self.timeout_seconds) as response:
                    status = int(getattr(response, "status", getattr(response, "code", 200)))
                    body = response.read()
                if _retryable(status) and attempt < self.max_retries:
                    time.sleep(min(0.2, 0.05 * (2**attempt)))
                    continue
                if status >= 400:
                    raise QwenProviderError(
                        f"Qwen request failed with status={status}", status_code=status
                    )
                return body, attempt + 1
            except urllib_error.HTTPError as exc:
                if not _retryable(exc.code) or attempt >= self.max_retries:
                    raise _http_error(exc) from exc
                last_error = exc
                time.sleep(min(0.2, 0.05 * (2**attempt)))
            except TimeoutError as exc:
                if attempt >= self.max_retries:
                    raise QwenProviderError("Qwen request timed out") from exc
                last_error = exc
                time.sleep(min(0.2, 0.05 * (2**attempt)))
            except OSError as exc:
                if attempt >= self.max_retries:
                    raise QwenProviderError("Qwen request failed") from exc
                last_error = exc
                time.sleep(min(0.2, 0.05 * (2**attempt)))
        raise QwenProviderError("Qwen request failed after retry") from last_error


def qwen_readonly_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "detect_issues",
                "description": "Detect data quality issues from a dataset profile and samples.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "dataset_name": {"type": "string"},
                        "issue_focus": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["dataset_name", "issue_focus"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "build_repair_plan",
                "description": "Build a dry-run repair plan from detected issue types.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "dataset_name": {"type": "string"},
                        "allowed_actions": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["dataset_name", "allowed_actions"],
                },
            },
        },
    ]


def _parse_response(response_body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(response_body.decode("utf-8"))
        message = payload["choices"][0]["message"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise QwenProviderError("Qwen response shape is invalid") from exc

    content = _normalize_text_content(message.get("content") or "")
    tool_calls = message.get("tool_calls") or []
    if not isinstance(tool_calls, list):
        raise QwenProviderError("Qwen tool_calls must be a list")
    return {
        "content": content,
        "tool_calls": tool_calls,
        "usage": payload.get("usage", {}),
        "response_id": str(payload.get("id") or ""),
        "model": str(payload.get("model") or ""),
    }


_TEXT_ENVELOPE_KEYS = {"type", "text", "annotations", "meta"}


def _normalize_text_content(content: Any) -> str:
    parsed = content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return content
        if not isinstance(parsed, (dict, list)):
            return content

    envelope: Any = None
    if (
        isinstance(parsed, dict)
        and parsed.get("type") == "text"
        and set(parsed).issubset(_TEXT_ENVELOPE_KEYS)
    ):
        envelope = parsed
    elif (
        isinstance(parsed, list)
        and len(parsed) == 1
        and isinstance(parsed[0], dict)
        and parsed[0].get("type") == "text"
        and set(parsed[0]).issubset(_TEXT_ENVELOPE_KEYS)
    ):
        envelope = parsed[0]

    if envelope is not None:
        text = envelope.get("text")
        if not isinstance(text, str):
            raise QwenProviderError("Qwen text envelope is invalid")
        return text
    if isinstance(content, str):
        return content
    raise QwenProviderError("Qwen content must be text")


def _retryable(status_code: int) -> bool:
    return status_code in {408, 429} or status_code >= 500


def _http_error(exc: urllib_error.HTTPError) -> QwenProviderError:
    error_code = ""
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        error_code = str(error.get("code") or "")
    except Exception:
        error_code = ""
    return QwenProviderError(
        f"Qwen request failed with status={exc.code}",
        status_code=exc.code,
        error_code=error_code,
    )
