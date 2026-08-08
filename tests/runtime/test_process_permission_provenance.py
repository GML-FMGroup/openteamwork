"""Trusted process provenance storage tests."""

from __future__ import annotations

import sys
from pathlib import Path

from openppx.runtime.process_sessions import ProcessSessionManager


def test_process_session_manager_records_node_supplied_provenance(tmp_path: Path) -> None:
    manager = ProcessSessionManager()
    session, _ = manager.start_session(
        command="python -c pass",
        argv=[sys.executable, "-c", "pass"],
        cwd=tmp_path,
        env=None,
        use_pty=False,
        scope_key="task-a",
        created_by_agent_id="worker",
        created_by_task_id="task-a",
        created_by_run_id="run-a",
        permission_revision_at_start="sha256:" + "a" * 64,
        execution_profile="medium-task-sandbox",
        created_by_allowed_command=True,
    )
    manager.mark_backgrounded(session.session_id, scope_key="task-a")

    snapshot = manager.describe_session(session.session_id, scope_key="task-a")

    assert snapshot is not None
    assert snapshot.created_by_agent_id == "worker"
    assert snapshot.created_by_task_id == "task-a"
    assert snapshot.created_by_run_id == "run-a"
    assert snapshot.execution_profile == "medium-task-sandbox"
    assert snapshot.created_by_allowed_command is True
    manager.remove_session(session.session_id, scope_key="task-a")
