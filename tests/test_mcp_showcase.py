from __future__ import annotations

import tomllib
from pathlib import Path

from dataops_agent.mcp_showcase import build_mcp_showcase


def test_mcp_showcase_has_packaged_entrypoint():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert (
        config["project"]["scripts"]["dataops-agent-mcp-showcase"]
        == "dataops_agent.mcp_showcase:main"
    )


def test_mcp_showcase_exports_tools_resources_prompts_and_boundary():
    report = build_mcp_showcase(generated_at="2026-08-24T00:00:00+00:00")

    assert report["status"] == "ok"
    assert report["tool_count"] == 9
    assert report["resource_count"] >= 4
    assert report["resource_template_count"] >= 1
    assert report["prompt_count"] == 2
    assert report["missing_expected_surface"] == {
        "tools": [],
        "resources": [],
        "resource_templates": [],
        "prompts": [],
    }
    assert report["forbidden_tools_present"] == []
    assert report["mcp_boundary"]["can_publish"] is False
    assert report["mcp_boundary"]["can_rollback"] is False
    assert report["mcp_boundary"]["can_execute_shell"] is False
