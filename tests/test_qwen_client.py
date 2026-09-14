from __future__ import annotations

import io
import json
import os
from urllib.error import HTTPError

import pytest

from dataops_agent.qwen_client import QwenOpenAICompatibleClient, QwenProviderError
from dataops_agent.qwen_readiness import run_qwen_readiness

pytestmark = [pytest.mark.unit, pytest.mark.ai_eval]


class FakeResponse:
    status = 200

    def __init__(self, payload: dict):
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_qwen_client_requires_key(monkeypatch):
    monkeypatch.delenv("DATAOPS_QWEN_API_KEY", raising=False)
    client = QwenOpenAICompatibleClient()
    with pytest.raises(QwenProviderError):
        client.chat(messages=[{"role": "user", "content": "ping"}], max_tokens=16)


def test_qwen_client_exposes_sanitized_http_error(qwen_api_key):
    def fake_urlopen(_request, timeout):
        assert timeout == 30
        body = io.BytesIO(
            json.dumps({"error": {"code": "invalid_api_key", "message": "bad key"}}).encode("utf-8")
        )
        raise HTTPError("https://example.test", 401, "unauthorized", {}, body)

    client = QwenOpenAICompatibleClient(urlopen=fake_urlopen)
    with pytest.raises(QwenProviderError) as exc:
        client.chat(messages=[{"role": "user", "content": "ping"}], max_tokens=16)
    assert exc.value.status_code == 401
    assert exc.value.error_code == "invalid_api_key"


def test_qwen_client_parses_tool_calls(qwen_api_key):
    def fake_urlopen(_request, timeout):
        assert timeout == 30
        return FakeResponse(
            {
                "id": "resp_1",
                "model": "qwen-test",
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "detect_issues",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            }
        )

    response = QwenOpenAICompatibleClient(model="qwen-test", urlopen=fake_urlopen).chat(
        messages=[{"role": "user", "content": "pick a tool"}],
        max_tokens=32,
    )
    assert response.tool_calls[0]["function"]["name"] == "detect_issues"
    assert response.usage["total_tokens"] == 13


def test_qwen_client_retries_429(qwen_api_key):
    attempts = {"count": 0}

    def fake_urlopen(_request, timeout):
        assert timeout == 30
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise HTTPError("https://example.test", 429, "too many", {}, io.BytesIO())
        return FakeResponse(
            {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"total_tokens": 1},
            }
        )

    response = QwenOpenAICompatibleClient(urlopen=fake_urlopen, max_retries=1).chat(
        messages=[{"role": "user", "content": "ping"}],
        max_tokens=16,
    )
    assert response.content == "ok"
    assert response.attempts == 2


def test_qwen_client_unwraps_openai_text_content_envelope(qwen_api_key):
    inner = json.dumps({"decision": "manual_review"})

    def fake_urlopen(_request, timeout):
        assert timeout == 30
        return FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "type": "text",
                                    "text": inner,
                                    "annotations": [],
                                    "meta": {},
                                }
                            )
                        }
                    }
                ],
                "usage": {"total_tokens": 10},
            }
        )

    response = QwenOpenAICompatibleClient(urlopen=fake_urlopen).chat(
        messages=[{"role": "user", "content": "return json"}],
        max_tokens=32,
    )

    assert response.content == inner


def test_qwen_client_disables_thinking_for_bounded_agent_calls(qwen_api_key):
    captured = {}

    def fake_urlopen(request, timeout):
        assert timeout == 30
        captured.update(json.loads(request.data.decode("utf-8")))
        return FakeResponse(
            {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"total_tokens": 1},
            }
        )

    QwenOpenAICompatibleClient(urlopen=fake_urlopen).chat(
        messages=[{"role": "user", "content": "ping"}],
        max_tokens=16,
    )

    assert captured["enable_thinking"] is False


def test_qwen_readiness_report_never_records_key(monkeypatch, tmp_path, qwen_api_key):
    os.environ["DATAOPS_QWEN_API_KEY"] = "test-key"

    def fake_urlopen(_request, timeout):
        assert timeout == 30
        return FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "ok",
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {"name": "detect_issues", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
            }
        )

    original_init = QwenOpenAICompatibleClient.__init__

    def patched_init(self, **kwargs):
        original_init(self, **kwargs, urlopen=fake_urlopen)

    monkeypatch.setattr(QwenOpenAICompatibleClient, "__init__", patched_init)
    report = tmp_path / "report.json"
    payload = run_qwen_readiness(report_path=report)
    text = report.read_text(encoding="utf-8")
    assert payload["headline_metrics"]["live_request_count"] == 2
    assert "test-key" not in text
