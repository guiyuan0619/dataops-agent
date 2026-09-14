from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio

from dataops_agent.mcp_server import _mcp_boundary, mcp

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORT_JSON_PATH = PROJECT_ROOT / "reports" / "dataops_mcp_showcase.json"
REPORT_MD_PATH = PROJECT_ROOT / "reports" / "dataops_mcp_showcase.md"

EXPECTED_TOOLS = {
    "dataops_list_datasets",
    "dataops_get_dataset_contract",
    "dataops_profile_dataset",
    "dataops_validate_dataset",
    "dataops_preview_repair",
    "dataops_start_governance_run",
    "dataops_governance_status",
    "dataops_version_status",
    "dataops_get_trace",
}
EXPECTED_RESOURCES = {
    "dataops://datasets",
    "dataops://governance/boundary",
    "dataops://skills",
    "dataops://scorecard/latest",
}
EXPECTED_RESOURCE_TEMPLATES = {"dataops://datasets/{dataset_name}/contract"}
EXPECTED_PROMPTS = {"dataops_quality_triage", "dataops_release_risk_review"}
FORBIDDEN_TOOLS = {
    "approve_governance_run",
    "publish_version",
    "rollback_version",
    "resume_governance_run",
    "execute_shell",
}


def build_mcp_showcase(generated_at: str | None = None) -> dict[str, Any]:
    """Return the current MCP control-plane surface from FastMCP registration state."""

    surface = anyio.run(_collect_mcp_surface)
    tool_names = set(surface["tools"])
    resource_uris = set(surface["resources"])
    resource_templates = set(surface["resource_templates"])
    prompt_names = set(surface["prompts"])
    forbidden_present = sorted(tool_names & FORBIDDEN_TOOLS)
    missing = {
        "tools": sorted(EXPECTED_TOOLS - tool_names),
        "resources": sorted(EXPECTED_RESOURCES - resource_uris),
        "resource_templates": sorted(EXPECTED_RESOURCE_TEMPLATES - resource_templates),
        "prompts": sorted(EXPECTED_PROMPTS - prompt_names),
    }
    boundary = _mcp_boundary()
    status = (
        "ok"
        if (
            not forbidden_present
            and not any(missing.values())
            and boundary["can_publish"] is False
            and boundary["can_rollback"] is False
            and boundary["can_execute_shell"] is False
        )
        else "failed"
    )
    return {
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "status": status,
        "tool_count": len(tool_names),
        "resource_count": len(resource_uris),
        "resource_template_count": len(resource_templates),
        "prompt_count": len(prompt_names),
        "tools": sorted(tool_names),
        "resources": sorted(resource_uris),
        "resource_templates": sorted(resource_templates),
        "prompts": sorted(prompt_names),
        "missing_expected_surface": missing,
        "forbidden_tools_present": forbidden_present,
        "mcp_boundary": boundary,
        "interview_boundary": (
            "MCP is a governed control plane for discovery, contracts, diagnostics, repair "
            "preview, approval-pending governance starts, status, version status, and trace "
            "lookup. It does not approve, publish, rollback, execute shell, or write active "
            "versions."
        ),
    }


def write_mcp_showcase_reports(generated_at: str | None = None) -> dict[str, Any]:
    report = build_mcp_showcase(generated_at=generated_at)
    reports_dir = PROJECT_ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    REPORT_JSON_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    REPORT_MD_PATH.write_text(_render_markdown(report), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Export DataOps MCP control-plane showcase.")
    parser.add_argument("--generated-at", default=None)
    args = parser.parse_args(argv)
    report = write_mcp_showcase_reports(generated_at=args.generated_at)
    print(
        json.dumps(
            {
                "status": report["status"],
                "tool_count": report["tool_count"],
                "resource_count": report["resource_count"],
                "prompt_count": report["prompt_count"],
            }
        )
    )


async def _collect_mcp_surface() -> dict[str, list[str]]:
    tools = await mcp.list_tools()
    resources = await mcp.list_resources()
    resource_templates = await mcp.list_resource_templates()
    prompts = await mcp.list_prompts()
    return {
        "tools": [item.name for item in tools],
        "resources": [str(item.uri) for item in resources],
        "resource_templates": [str(item.uriTemplate) for item in resource_templates],
        "prompts": [item.name for item in prompts],
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# DataOps MCP Showcase",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Status: `{report['status']}`",
        f"- Tools: `{report['tool_count']}`",
        f"- Resources: `{report['resource_count']}`",
        f"- Resource templates: `{report['resource_template_count']}`",
        f"- Prompts: `{report['prompt_count']}`",
        "",
        "## Tools",
        "",
    ]
    lines.extend(f"- `{name}`" for name in report["tools"])
    lines.extend(["", "## Resources", ""])
    lines.extend(f"- `{uri}`" for uri in report["resources"])
    lines.extend(["", "## Resource Templates", ""])
    lines.extend(f"- `{uri}`" for uri in report["resource_templates"])
    lines.extend(["", "## Prompts", ""])
    lines.extend(f"- `{name}`" for name in report["prompts"])
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            report["interview_boundary"],
            "",
            f"- Forbidden tools present: `{report['forbidden_tools_present']}`",
            f"- Missing expected surface: `{report['missing_expected_surface']}`",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
