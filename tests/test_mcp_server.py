from __future__ import annotations

import json
import tomllib
from pathlib import Path

import anyio
import pytest

import dataops_agent.mcp_server as mcp_server
from dataops_agent.audit import IntegrationAuditStore
from dataops_agent.mcp_server import (
    controlled_start_governance_run,
    dataset_catalog_resource,
    dataset_contract_resource,
    governance_boundary_resource,
    list_datasets,
    mcp,
    quality_triage_prompt,
    readonly_dataset_contract,
    readonly_get_trace,
    readonly_governance_status,
    readonly_preview_repair,
    readonly_profile_dataset,
    readonly_validate_dataset,
    readonly_version_status,
    release_risk_review_prompt,
    skill_registry_resource,
)
from dataops_agent.versioning import VersionStore


def test_mcp_server_has_packaged_entrypoint():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert config["project"]["scripts"]["dataops-agent-mcp"] == "dataops_agent.mcp_server:main"


def test_mcp_tools_are_registered_as_readonly_surface():
    tools = anyio.run(mcp.list_tools)
    names = {tool.name for tool in tools}
    assert {
        "dataops_list_datasets",
        "dataops_get_dataset_contract",
        "dataops_profile_dataset",
        "dataops_validate_dataset",
        "dataops_preview_repair",
        "dataops_start_governance_run",
        "dataops_governance_status",
        "dataops_version_status",
        "dataops_get_trace",
    } <= names
    assert "publish_version" not in names
    assert "rollback_version" not in names
    assert "resume_governance_run" not in names
    assert "execute_shell" not in names


def test_mcp_resources_and_prompts_are_registered_as_reusable_context():
    resources = anyio.run(mcp.list_resources)
    resource_uris = {str(resource.uri) for resource in resources}
    assert {
        "dataops://datasets",
        "dataops://governance/boundary",
        "dataops://skills",
        "dataops://scorecard/latest",
    } <= resource_uris

    templates = anyio.run(mcp.list_resource_templates)
    template_uris = {str(template.uriTemplate) for template in templates}
    assert "dataops://datasets/{dataset_name}/contract" in template_uris

    prompts = anyio.run(mcp.list_prompts)
    prompt_names = {prompt.name for prompt in prompts}
    assert {"dataops_quality_triage", "dataops_release_risk_review"} <= prompt_names

    catalog = json.loads(dataset_catalog_resource())
    assert {item["dataset_name"] for item in catalog["datasets"]} == {
        "doctors",
        "hospitals",
        "service_requests",
    }
    contract = json.loads(dataset_contract_resource("nyc_311"))
    assert contract["dataset_name"] == "service_requests"
    boundary = json.loads(governance_boundary_resource())
    assert boundary["boundary"]["can_publish"] is False
    skills = json.loads(skill_registry_resource())
    assert skills["status"] == "ok"
    assert "Do not approve" in quality_triage_prompt("service_requests")
    assert "approval and publish actions must stay outside MCP" in release_risk_review_prompt()


def test_mcp_dataset_discovery_and_contract_describe_governance_surface():
    datasets = list_datasets()
    names = {item["dataset_name"] for item in datasets["datasets"]}
    assert names == {"doctors", "hospitals", "service_requests"}
    assert datasets["mcp_boundary"]["can_publish"] is False

    contract = readonly_dataset_contract("nyc_311")
    assert contract["dataset_name"] == "service_requests"
    assert contract["key_column"] == "request_id"
    assert contract["consumer_contract"]["name"] == "dataops_consumer_v1"
    assert "status" in contract["enum_rules"]


def test_mcp_profile_and_validate_project_dataset():
    profile = readonly_profile_dataset("data/raw/clean_dev.csv")
    report = readonly_validate_dataset("data/raw/clean_dev.csv")
    assert profile["row_count"] == 6
    assert report["passes_quality_gate"] is True


def test_mcp_preview_repair_does_not_apply_or_publish():
    preview = readonly_preview_repair("data/derived/dirty_dev.csv", dataset_name="doctors")
    assert preview["issue_count"] == 5
    assert preview["repair_action_count"] == 5
    assert len(preview["preview"]["changes"]) == 5
    assert preview["mcp_boundary"]["can_preview_repair"] is True
    assert preview["mcp_boundary"]["can_publish"] is False


def test_mcp_rejects_paths_outside_project():
    with pytest.raises(ValueError):
        readonly_profile_dataset("..\\seg_project\\AGENTS.md")


def test_mcp_version_status_is_readonly():
    status = readonly_version_status("unknown-dataset")
    assert status["dataset_name"] == "unknown-dataset"
    assert status["active_version"] is None
    assert status["version_ids"] == []


def test_mcp_start_governance_creates_pending_task_without_publish(tmp_path, monkeypatch):
    store = VersionStore(tmp_path / "dataops.sqlite3", tmp_path / "versions")
    audit = IntegrationAuditStore(tmp_path / "audit.sqlite3")
    monkeypatch.setattr(mcp_server, "DEFAULT_STORE", store)
    monkeypatch.setattr(mcp_server, "DEFAULT_AUDIT_STORE", audit)
    monkeypatch.setattr(mcp_server, "DEFAULT_CHECKPOINT_PATH", tmp_path / "checkpoints.sqlite3")

    result = controlled_start_governance_run(
        "data/derived/dirty_dev.csv",
        dataset_name="doctors",
        thread_id="mcp-thread-1",
        consumer_agent="mcp_client",
        trace_id="mcp-trace-1",
    )

    assert result["status"] == "awaiting_approval"
    assert result["thread_id"] == "mcp-thread-1"
    assert result["mcp_boundary"]["can_create_approval_pending_governance_run"] is True
    assert result["mcp_boundary"]["can_approve"] is False
    assert result["mcp_boundary"]["can_publish"] is False
    assert store.active_version("doctors") is None

    saved = readonly_governance_status("mcp-thread-1")
    assert saved["status"] == "awaiting_approval"
    assert saved["thread_id"] == "mcp-thread-1"

    trace = readonly_get_trace("mcp-trace-1")
    assert trace["events"][0]["operation"] == "governance_start"

    thread_trace = readonly_get_trace(thread_id="mcp-thread-1")
    assert thread_trace["trace_ids"] == ["mcp-trace-1"]
    assert thread_trace["events"][0]["thread_id"] == "mcp-thread-1"

    with pytest.raises(ValueError):
        readonly_get_trace()
