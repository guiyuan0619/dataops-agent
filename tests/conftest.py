from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass(frozen=True)
class GovernancePaths:
    clean: Path
    dirty: Path
    labels: Path
    checkpoint: Path
    version_db: Path
    artifact_dir: Path


@pytest.fixture
def api_client():
    from fastapi.testclient import TestClient

    from dataops_agent.api import app

    return TestClient(app)


@pytest.fixture
def qwen_api_key(monkeypatch):
    monkeypatch.setenv("DATAOPS_QWEN_API_KEY", "test-key")
    return "test-key"


@pytest.fixture
def dirty_doctor_governance_paths(tmp_path: Path) -> GovernancePaths:
    from dataops_agent.dirty_data import inject_dirty_doctor_data
    from tests.test_rules_repair_versioning import _clean_df

    paths = GovernancePaths(
        clean=tmp_path / "clean.csv",
        dirty=tmp_path / "dirty.csv",
        labels=tmp_path / "labels.json",
        checkpoint=tmp_path / "checkpoints.sqlite3",
        version_db=tmp_path / "versions.sqlite3",
        artifact_dir=tmp_path / "versions",
    )
    _clean_df().to_csv(paths.clean, index=False)
    inject_dirty_doctor_data(paths.clean, paths.dirty, paths.labels)
    return paths
