from __future__ import annotations

import tomllib
from pathlib import Path

from dataops_agent.skill_registry import skill_registry_snapshot


def test_skill_registry_has_packaged_entrypoint():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert (
        config["project"]["scripts"]["dataops-agent-skill-registry"]
        == "dataops_agent.skill_registry:main"
    )


def test_project_skills_are_reusable_and_bounded():
    report = skill_registry_snapshot(generated_at="2026-08-24T00:00:00+00:00")

    assert report["status"] == "ok"
    assert report["skill_count"] == 2
    assert report["documented_count"] == 2
    assert set(report["reusable_workflows"]) == {
        "hospital-knowledge-release",
        "table-quality-check",
    }
    assert report["mcp_alignment"]["blocks_mcp_publish"] is True

    for skill in report["skills"]:
        assert skill["status"] == "ok"
        assert skill["documented_in_readme"] is True
        assert skill["name_matches_folder"] is True
        assert all(skill["sections"].values())
        assert skill["safety_flags"]["blocks_direct_publish"] is True
        assert skill["safety_flags"]["blocks_shell_or_arbitrary_write"] is True
        assert skill["safety_flags"]["protects_live_model_usage"] is True
