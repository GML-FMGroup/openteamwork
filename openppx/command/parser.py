"""Argument parser for the converged OpenTeamwork command surface."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from openppx.product import PRODUCT
from openppx.runtime.paths import default_node_root


_CLIENT_API_URL_ENV = f"{PRODUCT.environment_prefix}_CLIENT_API_URL"
_CLIENT_API_TOKEN_ENV = f"{PRODUCT.environment_prefix}_CLIENT_API_TOKEN"
_DEFAULT_CLIENT_API_URL = f"http://127.0.0.1:{PRODUCT.default_client_api_port}"


def _transport_parent() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--url",
        dest="client_api_url",
        default=os.getenv(_CLIENT_API_URL_ENV, _DEFAULT_CLIENT_API_URL),
        help=f"{PRODUCT.display_name} Node URL (default: {_DEFAULT_CLIENT_API_URL}).",
    )
    parser.add_argument(
        "--token",
        dest="access_token",
        default=os.getenv(_CLIENT_API_TOKEN_ENV, ""),
        help="Bearer token for a protected Node.",
    )
    parser.add_argument("--json", dest="output_json", action="store_true", help="Emit machine-readable JSON.")
    return parser


def _add_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("kind", choices=["plugin", "skill"])
    parser.add_argument("source_type", choices=["local_directory", "local_archive", "git", "catalog"])
    parser.add_argument("locator")
    parser.add_argument("--source-version", default=None)
    parser.add_argument("--source-revision", default=None)
    parser.add_argument("--source-provider", default=None)
    parser.add_argument("--source-subpath", default=None)


def build_parser() -> argparse.ArgumentParser:
    """Build the small stable CLI parser without importing business services."""
    transport = _transport_parent()
    parser = argparse.ArgumentParser(
        prog=PRODUCT.cli_command,
        description=f"Operate {PRODUCT.display_name} Nodes and Agents.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "status",
        parents=[transport],
        help="Show the same Node overview as 'operations status'.",
    )

    setup = subparsers.add_parser("setup", help="Configure a Node, Agent, Model Profile, and first Hello.")
    setup.add_argument("--node-root", type=Path, default=default_node_root())
    setup.add_argument("--url", dest="client_api_url", default="", help="Configure a running Node instead of a local root.")
    setup.add_argument("--token", dest="access_token", default=os.getenv(_CLIENT_API_TOKEN_ENV, ""))
    setup.add_argument("--node-id", default="local-node")
    setup.add_argument("--node-name", default="")
    setup.add_argument("--agent-id", default=PRODUCT.default_agent_id)
    setup.add_argument("--agent-name", default=PRODUCT.default_agent_display_name)
    setup.add_argument("--user-id", default="ppx-client-user")
    setup.add_argument("--workspace", default="")
    setup.add_argument(
        "--privilege-level",
        choices=PRODUCT.allowed_agent_privilege_levels,
        default=PRODUCT.default_agent_privilege_level,
    )
    setup.add_argument("--profile-id", default="primary")
    setup.add_argument("--provider", default="google")
    setup.add_argument("--model", default="")
    setup.add_argument("--execution-location", choices=["local", "remote"], default="remote")
    setup.add_argument("--credential-name", default="primary-model-api-key")
    setup.add_argument("--api-key", default="", help="Provider API key; prefer the hidden interactive prompt.")
    setup.add_argument("--listen-host", default="127.0.0.1")
    setup.add_argument("--listen-port", type=int, default=PRODUCT.default_client_api_port)
    setup.add_argument("--authentication", choices=["required", "disabled"], default="disabled")
    setup.add_argument("--hello", default=f"Hello {PRODUCT.display_name}")
    setup.add_argument("--no-hello", action="store_true")
    setup.add_argument("--json", dest="output_json", action="store_true")

    node = subparsers.add_parser("node", help=f"Run or install the long-lived {PRODUCT.display_name} Node.")
    node_sub = node.add_subparsers(dest="node_command", required=True)
    node_run = node_sub.add_parser("run", help="Run the Node in the foreground.")
    node_run.add_argument("--node-root", type=Path, default=default_node_root())
    node_run.add_argument("--host", default=None)
    node_run.add_argument("--port", type=int, default=None)
    node_run.add_argument("--token", dest="access_token", default=None)
    node_service = node_sub.add_parser("service", help="Manage the OS user-service manifest.")
    service_sub = node_service.add_subparsers(dest="service_command", required=True)
    service_install = service_sub.add_parser("install", help="Install a user-service manifest.")
    service_install.add_argument("--node-root", type=Path, default=default_node_root())
    service_install.add_argument("--force", action="store_true")
    service_status = service_sub.add_parser("status", help="Inspect the user-service manifest.")
    service_status.add_argument("--json", dest="output_json", action="store_true")

    user = subparsers.add_parser("user", help="Manage App users locally on this Node.")
    user_sub = user.add_subparsers(dest="user_command", required=True)
    user_add = user_sub.add_parser("add", help="Create one immutable-login App user.")
    user_add.add_argument("email")
    user_add.add_argument("--privilege", dest="privilege_level", choices=PRODUCT.allowed_agent_privilege_levels, required=True)
    user_add.add_argument("--secret-stdin", action="store_true", help="Read one secret line from stdin instead of prompting.")
    user_add.add_argument("--node-root", type=Path, default=default_node_root())
    user_add.add_argument("--json", dest="output_json", action="store_true")
    user_list = user_sub.add_parser("list", help="List credential-free App account summaries.")
    user_list.add_argument("--node-root", type=Path, default=default_node_root())
    user_list.add_argument("--json", dest="output_json", action="store_true")
    user_disable = user_sub.add_parser("disable", help="Permanently disable one App user and revoke sessions.")
    user_disable.add_argument("email")
    user_disable.add_argument("--node-root", type=Path, default=default_node_root())
    user_disable.add_argument("--yes", action="store_true", help="Confirm the permanent disable operation.")
    user_disable.add_argument("--json", dest="output_json", action="store_true")

    action = subparsers.add_parser("action", help="Discover and invoke product Actions.")
    action_sub = action.add_subparsers(dest="action_command", required=True)
    action_list = action_sub.add_parser("list", parents=[transport], help="List caller-visible Actions.")
    action_list.add_argument("--namespace", default=None)
    action_list.add_argument("--projection", choices=["cli", "slash", "desktop", "mobile"], default=None)
    action_invoke = action_sub.add_parser("invoke", parents=[transport], help="Invoke one Action by stable id.")
    action_invoke.add_argument("action_id")
    action_invoke.add_argument("--input-json", default="{}")
    action_invoke.add_argument("--yes", action="store_true")

    command = subparsers.add_parser("command", parents=[transport], help="Invoke an Action-backed slash command.")
    command.add_argument("raw_command")
    command.add_argument("--user-id", default="ppx-client-user")
    command.add_argument("--agent", dest="agent_id", default=None)
    command.add_argument("--session", dest="session_id", default=None)
    command.add_argument("--run", dest="run_id", default=None)

    goal = subparsers.add_parser("goal", help="Manage durable Goals and their TaskFlows through Actions.")
    goal_sub = goal.add_subparsers(dest="goal_command", required=True)
    goal_list = goal_sub.add_parser("list", parents=[transport], help="List caller-owned Goals.")
    goal_list.add_argument("--user", dest="user_id", default="ppx-client-user")
    goal_list.add_argument("--session", dest="session_id", default=None)
    goal_list.add_argument(
        "--status",
        dest="statuses",
        action="append",
        choices=["active", "waiting", "paused", "blocked", "completed", "cancelled", "failed"],
        default=[],
    )
    goal_list.add_argument("--limit", type=int, default=20)
    goal_show = goal_sub.add_parser("show", parents=[transport], help="Read one Goal and its active TaskFlow.")
    goal_show.add_argument("goal_id")
    goal_show.add_argument("--user", dest="user_id", default="ppx-client-user")
    goal_create = goal_sub.add_parser("create", parents=[transport], help="Create one Session-scoped Goal.")
    goal_create.add_argument("objective")
    goal_create.add_argument("--agent", dest="agent_id", required=True)
    goal_create.add_argument("--session", dest="session_id", required=True)
    goal_create.add_argument("--user", dest="user_id", default="ppx-client-user")
    goal_create.add_argument("--criterion", dest="completion_criteria", action="append", default=[])
    goal_create.add_argument("--constraint", dest="constraints", action="append", default=[])
    goal_create.add_argument("--workspace", dest="workspace_ref", default="")
    goal_create.add_argument("--budget-json", default="{}")
    goal_update = goal_sub.add_parser("update", parents=[transport], help="Update one unfinished Goal.")
    goal_update.add_argument("goal_id")
    goal_update.add_argument("expected_revision", type=int)
    goal_update.add_argument("--user", dest="user_id", default="ppx-client-user")
    goal_update.add_argument("--objective", default=None)
    goal_update.add_argument("--criterion", dest="completion_criteria", action="append", default=None)
    goal_update.add_argument("--constraint", dest="constraints", action="append", default=None)
    goal_update.add_argument("--budget-json", default=None)
    for name in ("pause", "resume", "cancel"):
        item = goal_sub.add_parser(name, parents=[transport], help=f"{name.title()} one Goal.")
        item.add_argument("goal_id")
        item.add_argument("expected_revision", type=int)
        item.add_argument("--user", dest="user_id", default="ppx-client-user")
        item.add_argument("--reason", default="")
    goal_retry = goal_sub.add_parser("retry", parents=[transport], help="Retry a recoverable blocked Goal step.")
    goal_retry.add_argument("goal_id")
    goal_retry.add_argument("expected_revision", type=int)
    goal_retry.add_argument("--step", dest="step_id", default=None)
    goal_retry.add_argument("--user", dest="user_id", default="ppx-client-user")
    goal_complete = goal_sub.add_parser("complete", parents=[transport], help="Complete one Goal with durable evidence.")
    goal_complete.add_argument("goal_id")
    goal_complete.add_argument("expected_revision", type=int)
    goal_complete.add_argument("--user", dest="user_id", default="ppx-client-user")
    goal_complete.add_argument("--evidence-json", default="[]")
    goal_complete.add_argument("--user-confirmed", action="store_true")
    goal_complete.add_argument("--reason", default="")
    goal_history = goal_sub.add_parser("history", parents=[transport], help="Read append-only Goal history.")
    goal_history.add_argument("goal_id")
    goal_history.add_argument("--user", dest="user_id", default="ppx-client-user")
    goal_history.add_argument("--limit", type=int, default=100)

    automation = subparsers.add_parser("automation", help="Manage user-created Automations through Actions.")
    automation_sub = automation.add_subparsers(dest="automation_command", required=True)
    automation_list = automation_sub.add_parser("list", parents=[transport], help="List user-created Automations.")
    automation_list.add_argument("--user", dest="user_id", default="ppx-client-user")
    automation_list.add_argument("--status", dest="statuses", action="append", choices=["active", "paused", "blocked"], default=[])
    automation_list.add_argument("--limit", type=int, default=100)
    automation_show = automation_sub.add_parser("show", parents=[transport], help="Read one Automation.")
    automation_show.add_argument("automation_id")
    automation_show.add_argument("--user", dest="user_id", default="ppx-client-user")
    automation_create = automation_sub.add_parser("create", parents=[transport], help="Create one Automation Definition.")
    automation_create.add_argument("name")
    automation_create.add_argument("instructions")
    automation_create.add_argument("--agent", dest="agent_id", required=True)
    automation_create.add_argument("--user", dest="user_id", default="ppx-client-user")
    automation_create.add_argument("--description", default="")
    automation_create.add_argument("--workspace", dest="workspace_ref", default="")
    automation_create.add_argument("--model-profile", dest="model_profile_ref", default=None)
    automation_create.add_argument("--output", dest="output_requirements", action="append", default=[])
    automation_create.add_argument("--schedule-json", default=None, help="Schedule object: kind plus everySeconds, cronExpr, or atMs.")
    automation_create.add_argument("--permission-json", default="{}")
    automation_create.add_argument("--extension-json", default="{}")
    automation_create.add_argument("--delivery-json", default="{}")
    automation_create.add_argument("--yes", action="store_true", help="Confirm requested standing permissions.")
    automation_update = automation_sub.add_parser("update", parents=[transport], help="Update one Automation Definition.")
    automation_update.add_argument("automation_id")
    automation_update.add_argument("expected_revision", type=int)
    automation_update.add_argument("--user", dest="user_id", default="ppx-client-user")
    automation_update.add_argument("--name", default=None)
    automation_update.add_argument("--description", default=None)
    automation_update.add_argument("--instructions", default=None)
    automation_update.add_argument("--schedule-json", default=None)
    for name in ("pause", "resume", "delete"):
        item = automation_sub.add_parser(name, parents=[transport], help=f"{name.title()} one Automation.")
        item.add_argument("automation_id")
        item.add_argument("expected_revision", type=int)
        item.add_argument("--user", dest="user_id", default="ppx-client-user")
        if name == "delete":
            item.add_argument("--yes", action="store_true")
    automation_run = automation_sub.add_parser("run", parents=[transport], help="Run one Automation now.")
    automation_run.add_argument("automation_id")
    automation_run.add_argument("--user", dest="user_id", default="ppx-client-user")
    automation_run.add_argument("--input-json", default="{}")
    automation_history = automation_sub.add_parser("history", parents=[transport], help="Read Automation run history.")
    automation_history.add_argument("automation_id")
    automation_history.add_argument("--user", dest="user_id", default="ppx-client-user")
    automation_history.add_argument("--limit", type=int, default=50)
    automation_trigger = automation_sub.add_parser("trigger", parents=[transport], help="Submit one trusted local event.")
    automation_trigger.add_argument("automation_id")
    automation_trigger.add_argument("--event-key", required=True)
    automation_trigger.add_argument("--event-id", required=True)
    automation_trigger.add_argument("--input-json", default="{}")
    automation_trigger.add_argument("--user", dest="user_id", default="ppx-client-user")
    automation_templates = automation_sub.add_parser("templates", parents=[transport], help="List reviewed Automation templates.")

    config = subparsers.add_parser("config", help="Read, validate, preview, or apply strict Config resources.")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    for name in ("read", "validate", "preview", "apply"):
        item = config_sub.add_parser(name, parents=[transport], help=f"{name.title()} a Node or Agent config.")
        item.add_argument("--agent", dest="agent_id", default=None, help="Target Agent id; omit for Node config.")
        if name != "read":
            item.add_argument("candidate", help="Path to the strict JSON resource candidate.")
        if name in {"preview", "apply"}:
            item.add_argument("--expected-revision", required=True)

    model = subparsers.add_parser("model", help="Manage Model Profiles through Actions.")
    model_sub = model.add_subparsers(dest="model_command", required=True)
    model_sub.add_parser("list", parents=[transport], help="List Model Profiles.")
    model_read = model_sub.add_parser("read", parents=[transport], help="Read one Model Profile.")
    model_read.add_argument("profile_id")
    for name in ("readiness", "select"):
        item = model_sub.add_parser(name, parents=[transport], help=f"{name.title()} a model for one Agent.")
        item.add_argument("agent_id")
        item.add_argument("--role", choices=["fast", "reasoning", "vision"], default=None)
        item.add_argument("--run-override", default=None)
    model_apply = model_sub.add_parser("apply", parents=[transport], help="Create or update a Model Profile.")
    model_apply.add_argument("profile_id")
    model_apply.add_argument("candidate")
    model_apply.add_argument("--expected-revision", default=None)

    extension = subparsers.add_parser("extension", help="Inspect and manage Node extensions.")
    extension_sub = extension.add_subparsers(dest="extension_command", required=True)
    extension_list = extension_sub.add_parser("list", parents=[transport])
    extension_list.add_argument("--kind", choices=["plugin", "app", "mcp", "skill"], default=None)
    extension_list.add_argument("--agent", dest="agent_id", default=None)
    for name in ("get", "readiness"):
        item = extension_sub.add_parser(name, parents=[transport])
        item.add_argument("kind", choices=["plugin", "app", "mcp", "skill"])
        item.add_argument("extension_id")
    extension_preview = extension_sub.add_parser("preview", parents=[transport])
    _add_source_arguments(extension_preview)
    extension_install = extension_sub.add_parser("install", parents=[transport])
    _add_source_arguments(extension_install)
    extension_install.add_argument("expected_digest")
    extension_install.add_argument("--expected-revision", default=None)
    extension_install.add_argument("--yes", action="store_true")
    for name in ("enable", "disable"):
        item = extension_sub.add_parser(name, parents=[transport])
        item.add_argument("kind", choices=["plugin", "mcp", "skill"])
        item.add_argument("extension_id")
        item.add_argument("agent_id")
        item.add_argument("expected_revision")
        item.add_argument("--yes", action="store_true")
    extension_remove = extension_sub.add_parser("remove", parents=[transport])
    extension_remove.add_argument("kind", choices=["plugin", "mcp", "skill"])
    extension_remove.add_argument("extension_id")
    extension_remove.add_argument("expected_revision")
    extension_remove.add_argument("--yes", action="store_true")
    extension_author = extension_sub.add_parser(
        "author",
        help="Scaffold, validate, evaluate, or package a portable extension source.",
    )
    author_sub = extension_author.add_subparsers(dest="author_command", required=True)
    author_scaffold = author_sub.add_parser("scaffold", help="Create a standards-compliant source template.")
    author_scaffold.add_argument("kind", choices=["skill", "plugin", "app"])
    author_scaffold.add_argument("name")
    author_scaffold.add_argument("--destination", type=Path, default=Path.cwd())
    author_scaffold.add_argument("--description", required=True)
    author_scaffold.add_argument("--display-name", default=None)
    author_scaffold.add_argument("--developer", default=f"{PRODUCT.display_name} Developer")
    author_scaffold.add_argument("--json", dest="output_json", action="store_true")
    author_validate = author_sub.add_parser("validate", help="Validate with production extension parsers.")
    author_validate.add_argument("kind", choices=["skill", "plugin", "app"])
    author_validate.add_argument("source", type=Path)
    author_validate.add_argument("--json", dest="output_json", action="store_true")
    author_package = author_sub.add_parser("package", help="Create a deterministic validated ZIP package.")
    author_package.add_argument("kind", choices=["skill", "plugin", "app"])
    author_package.add_argument("source", type=Path)
    author_package.add_argument("output", type=Path)
    author_package.add_argument("--json", dest="output_json", action="store_true")
    author_eval = author_sub.add_parser("eval", help="Run an EvalSet with Google ADK AgentEvaluator.")
    author_eval.add_argument("evalset", type=Path)
    author_eval.add_argument("--agent-module", required=True)
    author_eval.add_argument("--agent-name", default=None)
    author_eval.add_argument("--num-runs", type=int, default=1)
    author_eval.add_argument("--validate-only", action="store_true")
    author_eval.add_argument("--json", dest="output_json", action="store_true")

    operations = subparsers.add_parser("operations", help="Inspect and control Node operations.")
    operations_sub = operations.add_subparsers(dest="operations_command", required=True)
    for name in ("status", "health"):
        operations_sub.add_parser(name, parents=[transport])
    tasks = operations_sub.add_parser("tasks", parents=[transport])
    tasks.add_argument("--session", dest="session_id", default=None)
    tasks.add_argument("--limit", type=int, default=20)
    cron = operations_sub.add_parser("cron", help="Manage Node Cron jobs.")
    cron_sub = cron.add_subparsers(dest="cron_command", required=True)
    cron_list = cron_sub.add_parser("list", parents=[transport])
    cron_list.add_argument("--enabled-only", dest="include_disabled", action="store_false", default=True)
    cron_list.add_argument("--history-limit", type=int, default=20)
    cron_create = cron_sub.add_parser("create", parents=[transport])
    cron_create.add_argument("--name", required=True)
    cron_create.add_argument("--agent", dest="agent_id", required=True)
    cron_create.add_argument("--user", dest="user_id", default="ppx-client-user")
    cron_create.add_argument("--message", required=True)
    schedule = cron_create.add_mutually_exclusive_group(required=True)
    schedule.add_argument("--every-seconds", type=int)
    schedule.add_argument("--cron-expression")
    schedule.add_argument("--at-ms", type=int)
    cron_create.add_argument("--timezone", default=None)
    cron_create.add_argument("--delete-after-run", action="store_true")
    cron_create.add_argument("--yes", action="store_true")
    for name in ("enable", "disable", "remove", "run"):
        item = cron_sub.add_parser(name, parents=[transport])
        item.add_argument("job_id")
        if name == "run":
            item.add_argument("--force", action="store_true")
        item.add_argument("--yes", action="store_true")
    heartbeat = operations_sub.add_parser("heartbeat", help="Inspect or run Node heartbeat.")
    heartbeat_sub = heartbeat.add_subparsers(dest="heartbeat_command", required=True)
    heartbeat_sub.add_parser("status", parents=[transport])
    heartbeat_run = heartbeat_sub.add_parser("run", parents=[transport])
    heartbeat_run.add_argument("--reason", default="manual")
    usage = operations_sub.add_parser("usage", parents=[transport])
    usage.add_argument("--limit", type=int, default=20)
    usage.add_argument("--provider", default=None)
    audit = operations_sub.add_parser("audit", parents=[transport])
    audit.add_argument("--limit", type=int, default=50)
    audit.add_argument("--actor", dest="actor_id", default=None)
    audit.add_argument("--agent", dest="agent_id", default=None)
    audit.add_argument("--run", dest="run_id", default=None)
    audit.add_argument("--extension", dest="extension_id", default=None)
    audit.add_argument("--action", dest="action_id", default=None)
    audit.add_argument("--outcome", default=None)
    return parser
