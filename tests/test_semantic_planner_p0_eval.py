from dataops_agent.qwen_client import QwenProviderError, QwenResponse
from dataops_agent.semantic_planner import QwenSemanticPlanner
from experiments.semantic_planner_p0_eval import evaluate


def test_independent_live_set_has_scale_domains_and_adversarial_cases():
    cases = evaluate.build_cases()

    assert len(cases) >= 20
    assert {case.dataset_name for case in cases} == {
        "doctors",
        "hospitals",
        "service_requests",
    }
    assert sum(case.expected_target is None for case in cases) >= 4
    assert sum(case.category == "prompt_injection" for case in cases) >= 2


def test_check_only_readiness_does_not_require_or_call_qwen(monkeypatch):
    monkeypatch.delenv("DATAOPS_QWEN_API_KEY", raising=False)
    args = evaluate.parse_args([])
    ready = evaluate.readiness(args, evaluate.build_cases())

    assert ready["ready"] is False
    assert ready["blocking_reason"] == "missing_key:DATAOPS_QWEN_API_KEY"


def test_planner_records_provider_and_prompt_version():
    class FakeClient:
        def chat(self, **kwargs):
            return QwenResponse(
                content=(
                    '{"decision":"manual_review","mappings":[],"confidence":0.4,'
                    '"reason":"ambiguous"}'
                ),
                tool_calls=[],
                usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
                response_id="response-1",
                model="qwen-test",
                latency_ms=1.0,
                attempts=1,
            )

    planner = QwenSemanticPlanner(FakeClient(), max_output_tokens=100)
    planner.propose({"groups": []})

    assert planner.call_records == [
        {
            "model": "qwen-test",
            "response_id": "response-1",
            "latency_ms": planner.call_records[0]["latency_ms"],
            "attempts": 1,
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            "prompt_version": "dataops_semantic_planner_v2",
        }
    ]


def test_planner_records_safe_provider_failure_metadata():
    class FailingClient:
        model = "qwen-test"

        def chat(self, **kwargs):
            raise QwenProviderError("request rejected", status_code=400, error_code="Arrearage")

    planner = QwenSemanticPlanner(FailingClient(), max_output_tokens=100)
    try:
        planner.propose({"groups": []})
    except QwenProviderError:
        pass
    else:
        raise AssertionError("provider failure must remain visible to the caller")

    assert planner.call_records == []
    assert planner.error_records[0]["status_code"] == 400
    assert planner.error_records[0]["error_code"] == "Arrearage"
