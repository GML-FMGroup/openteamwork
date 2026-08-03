from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


# Ensure local package imports resolve when pytest is launched via external wrappers.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def _bind_legacy_test_paths(monkeypatch: pytest.MonkeyPatch):
    """Route old leaf-level unit fixtures into the explicit Node path APIs.

    Production no longer reads these environment variables. A bounded test
    adapter remains here while older unit cases directly call leaf tools rather
    than constructing a Node composition. Architecture tests ensure this
    adapter cannot leak into the shipped package.
    """
    from openppx.runtime import browser_remote_provider, context_engine, paths, task_store
    from openppx.runtime import task_execution, workspace_bootstrap
    from openppx.runtime.artifact_service import ArtifactConfig
    from openppx.tooling import skills_adapter

    previous_node_root = paths.configure_node_root(None)
    original_default_node_root = paths.default_node_root

    def selected_node_root() -> Path:
        agent_home = os.environ.get("OPENPPX_AGENT_HOME")
        if agent_home:
            return Path(agent_home).expanduser().resolve(strict=False)
        task_database = os.environ.get("OPENPPX_TASK_DB_PATH")
        if task_database:
            return Path(task_database).expanduser().resolve(strict=False).parent
        artifact_root = os.environ.get("OPENPPX_ARTIFACTS_DIR")
        if artifact_root:
            return Path(artifact_root).expanduser().resolve(strict=False).parent
        workspace = os.environ.get("OPENPPX_WORKSPACE")
        if workspace:
            return Path(workspace).expanduser().resolve(strict=False)
        return original_default_node_root()

    def selected_task_database() -> Path:
        explicit = os.environ.get("OPENPPX_TASK_DB_PATH")
        if explicit:
            return Path(explicit).expanduser().resolve(strict=False)
        return selected_node_root() / "database" / "tasks.db"

    def selected_artifact_config() -> ArtifactConfig:
        explicit = os.environ.get("OPENPPX_ARTIFACTS_DIR")
        root = Path(explicit).expanduser() if explicit else selected_node_root() / "artifacts"
        root.mkdir(parents=True, exist_ok=True)
        return ArtifactConfig(enabled=True, root_dir=str(root.resolve(strict=False)))

    monkeypatch.setattr(paths, "default_node_root", selected_node_root)
    monkeypatch.setattr(skills_adapter, "default_node_root", selected_node_root)
    monkeypatch.setattr(workspace_bootstrap, "default_node_root", selected_node_root)
    monkeypatch.setattr(task_store, "task_db_path", selected_task_database)
    monkeypatch.setattr(context_engine, "task_db_path", selected_task_database)
    monkeypatch.setattr(browser_remote_provider, "task_db_path", selected_task_database)
    monkeypatch.setattr(task_execution, "load_artifact_config", selected_artifact_config)
    yield
    paths.configure_node_root(previous_node_root)
