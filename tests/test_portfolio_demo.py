from __future__ import annotations

import json
import os

import pytest

from dataops_agent.portfolio_demo import (
    PortfolioDemoError,
    _acceptance_gates,
    _audit_public_text,
    _create_unique_runtime_dir,
    _hospital_python,
    _without_live_model_credentials,
    run_portfolio_demo,
)


def test_portfolio_demo_combines_current_capabilities_without_live_calls(tmp_path):
    hospital_repo = tmp_path / "hospital"
    hospital_repo.mkdir()
    hospital_live = hospital_repo / "hospital-live.json"
    dataops_live = tmp_path / "dataops-live.json"
    hospital_live.write_text(
        json.dumps(
            {
                "status": "passed",
                "versions": {"prompt_version": "hospital_service_agent_planner_v3"},
                "headline_metrics": {
                    "case_count": 24,
                    "passed_count": 22,
                    "pre_gate_model_call_count": 0,
                    "external_action_execution_count": 0,
                    "provider_error_count": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    dataops_live.write_text(
        json.dumps(
            {
                "status": "passed",
                "versions": {"prompt_version": "dataops_semantic_planner_v2"},
                "headline_metrics": {
                    "case_count": 24,
                    "passed_count": 24,
                    "mapping_accuracy": 1.0,
                    "manual_review_accuracy": 1.0,
                    "approval_bypass_count": 0,
                    "provider_error_count": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    def hospital_runner(_repo, _output, _release_report):
        return {
            "dataops_preflight": {
                "status": "ready_for_reindex_after_human_approval",
                "ready_for_reindex": True,
                "knowledge_doc_count": 4,
                "passed_canary_case_count": 5,
            },
            "summary": {
                "chat_case_count": 4,
                "chat_full_trace": True,
                "idempotency_replayed": True,
                "conversation_turn_count": 4,
                "conversation_context_reused": True,
                "stale_version_blocked": True,
                "conversation_audit_events_recorded": True,
                "action_intent_status": "awaiting_hospital_authorization",
                "external_execution_status": "not_connected",
            }
        }

    def dataops_runner(**_kwargs):
        return {
            "status": "ok",
            "scope": {"qwen_used": False},
            "gates": {"approval": True, "consumer": True},
            "doctor_dataset": {
                "governance_final_status": "published",
                "downstream_hospital_agent_report": {"report_json": "release.json"},
            },
            "cross_domain_consumer_regression": {"status": "passed"},
            "trace": {"event_count": 8},
        }

    def recovery_runner(**_kwargs):
        return {
            "status": "ok",
            "metrics": {
                "passed_scenario_count": 6,
                "scenario_count": 6,
                "bad_candidate_blocked": True,
                "active_preserved_after_bad_publish": True,
                "rollback_requires_dataops_approval": True,
                "approved_rollback_executed": True,
                "restored_regression_passed": True,
            },
        }

    report = run_portfolio_demo(
        hospital_repo=hospital_repo,
        report_json=tmp_path / "portfolio.json",
        report_md=tmp_path / "portfolio.md",
        runtime_root=tmp_path / "runtime",
        run_id="test-portfolio",
        hospital_live_report=hospital_live,
        dataops_live_report=dataops_live,
        hospital_runner=hospital_runner,
        dataops_runner=dataops_runner,
        recovery_runner=recovery_runner,
    )

    assert report["status"] == "ok"
    assert all(report["gates"].values())
    assert report["scope"]["qwen_called_this_run"] is False
    assert report["scope"]["ecs_used_this_run"] is False
    assert report["public_output_audit"]["passed"] is True
    assert (tmp_path / "portfolio.json").exists()
    assert (tmp_path / "portfolio.md").exists()


def test_public_output_audit_rejects_secrets_and_personal_paths():
    fake_bearer = "Bearer " + "abcdefghijklm" + "nopqrstuvwxyz"
    fake_path = "E:" + "\\academic\\private\\report.json"
    fake_phone = "138" + "0013" + "8000"
    audit = _audit_public_text(
        f"{fake_bearer} {fake_path} {fake_phone}"
    )

    assert audit["passed"] is False
    assert audit["secret_pattern_hits"] == 1
    assert audit["personal_absolute_path_hits"] == 1
    assert audit["phone_pattern_hits"] == 1


def test_runtime_directory_isolated_when_run_id_is_reused(tmp_path):
    first = _create_unique_runtime_dir(tmp_path, "same-run")
    second = _create_unique_runtime_dir(tmp_path, "same-run")

    assert first.name == "same-run"
    assert second.name == "same-run-2"


@pytest.mark.parametrize("run_id", ["../escape", "a/b", "a\\b", "", "á"])
def test_portfolio_demo_rejects_unsafe_run_id_before_creating_runtime(tmp_path, run_id):
    with pytest.raises(PortfolioDemoError, match="run_id"):
        run_portfolio_demo(
            hospital_repo=tmp_path / "hospital",
            runtime_root=tmp_path / "runtime",
            run_id=run_id,
        )
    assert not (tmp_path / "runtime").exists()


def test_live_evidence_gate_rejects_failed_or_unexpected_report():
    components = {
        "hospital_http": {
            "status": "passed",
            "conversation_context_reused": True,
            "stale_version_blocked": True,
            "external_execution_status": "not_connected",
            "dataops_release_preflight_status": "ready_for_reindex_after_human_approval",
            "dataops_release_preflight_ready": True,
            "dataops_release_doc_count": 4,
            "dataops_release_canary_passed_count": 5,
        },
        "dataops_governance": {"status": "passed"},
        "cross_project_recovery": {"status": "passed"},
        "live_model_evidence": {
            "status": "failed",
            "hospital": {
                "prompt_version": "hospital_service_agent_planner_v3",
                "passed_count": 22,
                "case_count": 24,
                "pre_gate_model_call_count": 0,
                "external_action_execution_count": 0,
                "provider_error_count": 0,
            },
            "dataops": {
                "prompt_version": "dataops_semantic_planner_v2",
                "passed_count": 24,
                "case_count": 24,
                "mapping_accuracy": 1.0,
                "manual_review_accuracy": 1.0,
                "approval_bypass_count": 0,
                "provider_error_count": 0,
            },
        },
    }

    gates = _acceptance_gates(components)
    assert gates["hospital_live_evidence_passed"] is False
    assert gates["dataops_live_evidence_passed"] is False

    components["live_model_evidence"]["status"] = "passed"
    components["live_model_evidence"]["hospital"]["prompt_version"] = "wrong"
    gates = _acceptance_gates(components)
    assert gates["hospital_live_evidence_passed"] is False
    assert gates["dataops_live_evidence_passed"] is True


def test_demo_disables_live_credentials_and_restores_environment(monkeypatch):
    monkeypatch.setenv("DATAOPS_SEMANTIC_PLANNER_MODE", "selective")
    monkeypatch.setenv("DATAOPS_QWEN_API_KEY", "fake-local-test-secret")

    with pytest.raises(RuntimeError), _without_live_model_credentials():
        assert os.environ["DATAOPS_SEMANTIC_PLANNER_MODE"] == "disabled"
        assert "DATAOPS_QWEN_API_KEY" not in os.environ
        raise RuntimeError("check restoration")

    assert os.environ["DATAOPS_SEMANTIC_PLANNER_MODE"] == "selective"
    assert os.environ["DATAOPS_QWEN_API_KEY"] == "fake-local-test-secret"


def test_hospital_python_requires_its_own_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("HOSPITAL_AGENT_PYTHON", raising=False)
    with pytest.raises(PortfolioDemoError, match="hospital Python environment"):
        _hospital_python(tmp_path)

    interpreter = tmp_path / "python.exe"
    interpreter.touch()
    monkeypatch.setenv("HOSPITAL_AGENT_PYTHON", str(interpreter))
    assert _hospital_python(tmp_path) == interpreter
