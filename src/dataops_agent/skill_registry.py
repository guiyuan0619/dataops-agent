from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = PROJECT_ROOT / ".paicli" / "skills"
REPORT_JSON_PATH = PROJECT_ROOT / "reports" / "dataops_skill_registry.json"
REPORT_MD_PATH = PROJECT_ROOT / "reports" / "dataops_skill_registry.md"

REQUIRED_SECTIONS = ("## Goal", "## Required Steps", "## Boundaries")


def skill_registry_snapshot(
    project_root: Path = PROJECT_ROOT,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Return a reusable Skill registry self-check without mutating project state."""

    skill_root = project_root / ".paicli" / "skills"
    readme_path = skill_root / "README.md"
    readme_text = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""

    skills = []
    if skill_root.exists():
        for skill_file in sorted(skill_root.glob("*/SKILL.md")):
            skills.append(_skill_summary(skill_file, project_root, readme_text))

    status = "ok" if skills and all(skill["status"] == "ok" for skill in skills) else "failed"
    documented_count = sum(1 for skill in skills if skill["documented_in_readme"])
    return {
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "status": status,
        "skill_count": len(skills),
        "documented_count": documented_count,
        "registry_readme": _relative(readme_path, project_root),
        "reusable_workflows": [skill["name"] for skill in skills],
        "mcp_alignment": {
            "uses_mcp_as_control_plane": "MCP is a governed DataOps control plane"
            in readme_text,
            "blocks_mcp_publish": "MCP must not approve, publish, rollback" in readme_text,
            "requires_human_approval": "human approval" in readme_text.lower(),
        },
        "skills": skills,
        "interview_boundary": (
            "Skills are reusable project workflows for Agent clients. They do not grant "
            "approval, publish, rollback, shell, or active-version write permissions."
        ),
    }


def write_skill_registry_reports(
    project_root: Path = PROJECT_ROOT,
    generated_at: str | None = None,
) -> dict[str, Any]:
    report = skill_registry_snapshot(project_root=project_root, generated_at=generated_at)
    reports_dir = project_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (project_root / "reports" / "dataops_skill_registry.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (project_root / "reports" / "dataops_skill_registry.md").write_text(
        _render_markdown(report),
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Validate reusable DataOps project Skills.")
    parser.add_argument("--generated-at", default=None)
    args = parser.parse_args(argv)
    report = write_skill_registry_reports(generated_at=args.generated_at)
    print(json.dumps({"status": report["status"], "skill_count": report["skill_count"]}))


def _skill_summary(skill_file: Path, project_root: Path, readme_text: str) -> dict[str, Any]:
    text = skill_file.read_text(encoding="utf-8")
    frontmatter = _parse_frontmatter(text)
    folder_name = skill_file.parent.name
    name = frontmatter.get("name", folder_name)
    section_flags = {
        section.removeprefix("## ").lower().replace(" ", "_"): section in text
        for section in REQUIRED_SECTIONS
    }
    boundary_lines = _extract_section_lines(text, "## Boundaries")
    safety_flags = _safety_flags(text, boundary_lines)

    status = (
        "ok"
        if (
            name == folder_name
            and all(section_flags.values())
            and bool(frontmatter.get("description"))
            and name in readme_text
            and safety_flags["blocks_direct_publish"]
            and safety_flags["blocks_shell_or_arbitrary_write"]
            and safety_flags["protects_live_model_usage"]
        )
        else "failed"
    )
    return {
        "name": name,
        "path": _relative(skill_file, project_root),
        "description": frontmatter.get("description", ""),
        "documented_in_readme": name in readme_text,
        "name_matches_folder": name == folder_name,
        "sections": section_flags,
        "boundary_assertions": boundary_lines,
        "safety_flags": safety_flags,
        "status": status,
    }


def _parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    frontmatter: dict[str, str] = {}
    for raw_line in text[3:end].splitlines():
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        frontmatter[key.strip()] = value.strip()
    return frontmatter


def _extract_section_lines(text: str, heading: str) -> list[str]:
    marker = text.find(heading)
    if marker == -1:
        return []
    section = text[marker + len(heading) :]
    next_heading = section.find("\n## ")
    if next_heading != -1:
        section = section[:next_heading]
    lines = []
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if line.startswith("- "):
            lines.append(line[2:].strip())
    return lines


def _safety_flags(text: str, boundary_lines: list[str]) -> dict[str, bool]:
    lowered = text.lower()
    boundary_text = " ".join(boundary_lines).lower()
    return {
        "blocks_direct_publish": _has_any(
            lowered,
            (
                "do not publish",
                "cannot publish",
                "no active version claim",
                "direct release permissions",
                "approval",
            ),
        ),
        "blocks_shell_or_arbitrary_write": _has_any(
            lowered,
            (
                "shell",
                "arbitrary file write",
                "direct publish",
                "active version",
            ),
        ),
        "protects_live_model_usage": (
            ("qwen" not in lowered)
            or _has_any(
                boundary_text,
                ("do not call qwen", "user has approved", "requires approval"),
            )
        ),
    }


def _has_any(value: str, options: tuple[str, ...]) -> bool:
    return any(option in value for option in options)


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# DataOps Skill Registry",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Status: `{report['status']}`",
        f"- Skill count: `{report['skill_count']}`",
        f"- Documented in README: `{report['documented_count']}/{report['skill_count']}`",
        "",
        "## Reusable Workflows",
        "",
        "| Skill | Status | Boundary |",
        "| --- | --- | --- |",
    ]
    for skill in report["skills"]:
        boundary = "; ".join(skill["boundary_assertions"])
        lines.append(f"| `{skill['name']}` | `{skill['status']}` | {boundary} |")

    lines.extend(
        [
            "",
            "## MCP Alignment",
            "",
            "- Uses MCP as control plane: "
            f"`{report['mcp_alignment']['uses_mcp_as_control_plane']}`",
            f"- Blocks MCP publish: `{report['mcp_alignment']['blocks_mcp_publish']}`",
            f"- Requires human approval: `{report['mcp_alignment']['requires_human_approval']}`",
            "",
            "## Interview Boundary",
            "",
            report["interview_boundary"],
            "",
        ]
    )
    return "\n".join(lines)


def _relative(path: Path, project_root: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.as_posix()


if __name__ == "__main__":
    main()
