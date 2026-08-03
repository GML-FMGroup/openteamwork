"""Argument parser for the converged OpenPPX command surface."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from openppx.runtime.paths import default_node_root


def _transport_parent() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--url",
        dest="client_api_url",
        default=os.getenv("OPENPPX_CLIENT_API_URL", "http://127.0.0.1:18765"),
        help="OpenPPX Node URL (default: http://127.0.0.1:18765).",
    )
    parser.add_argument(
        "--token",
        dest="access_token",
        default=os.getenv("OPENPPX_CLIENT_API_TOKEN", ""),
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
    parser = argparse.ArgumentParser(prog="ppx", description="Operate OpenPPX Nodes and Agents.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup = subparsers.add_parser("setup", help="Configure a Node, Agent, Model Profile, and first Hello.")
    setup.add_argument("--node-root", type=Path, default=default_node_root())
    setup.add_argument("--url", dest="client_api_url", default="", help="Configure a running Node instead of a local root.")
    setup.add_argument("--token", dest="access_token", default=os.getenv("OPENPPX_CLIENT_API_TOKEN", ""))
    setup.add_argument("--node-id", default="local-node")
    setup.add_argument("--node-name", default="")
    setup.add_argument("--agent-id", default="main")
    setup.add_argument("--agent-name", default="Main")
    setup.add_argument("--user-id", default="ppx-client-user")
    setup.add_argument("--workspace", default="")
    setup.add_argument("--privilege-level", choices=["low", "medium", "high", "root"], default="medium")
    setup.add_argument("--profile-id", default="primary")
    setup.add_argument("--provider", default="google")
    setup.add_argument("--model", default="")
    setup.add_argument("--execution-location", choices=["local", "remote"], default="remote")
    setup.add_argument("--credential-name", default="primary-model-api-key")
    setup.add_argument("--api-key", default="", help="Provider API key; prefer the hidden interactive prompt.")
    setup.add_argument("--listen-host", default="127.0.0.1")
    setup.add_argument("--listen-port", type=int, default=18765)
    setup.add_argument("--authentication", choices=["required", "disabled"], default="disabled")
    setup.add_argument("--hello", default="Hello OpenPPX")
    setup.add_argument("--no-hello", action="store_true")
    setup.add_argument("--json", dest="output_json", action="store_true")

    node = subparsers.add_parser("node", help="Run or install the long-lived OpenPPX Node.")
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
