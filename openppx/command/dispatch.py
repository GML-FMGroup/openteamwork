"""Translate parsed CLI commands into Node Actions or process lifecycle calls."""

from __future__ import annotations

from typing import Any

from openppx.runtime.node_host import run_node

from .service import install_node_service, node_service_status
from .setup import run_setup
from .transport import action_catalog, invoke_action, parse_json_object, read_json_object


def _source_input(args: Any) -> dict[str, object]:
    return {
        key: value
        for key, value in {
            "type": args.source_type,
            "locator": args.locator,
            "version": args.source_version,
            "revision": args.source_revision,
            "provider": args.source_provider,
            "subpath": args.source_subpath,
        }.items()
        if value not in {None, ""}
    }


def _dispatch_config(args: Any) -> int:
    suffix = "agent" if args.agent_id else "node"
    action_id = f"config.{suffix}.{args.config_command}"
    raw: dict[str, object] = {}
    if args.agent_id:
        raw["agentId"] = args.agent_id
    if args.config_command != "read":
        raw["candidate"] = read_json_object(args.candidate)
    if args.config_command in {"preview", "apply"}:
        raw["expectedRevision"] = args.expected_revision
    return invoke_action(args, action_id, raw)


def _dispatch_model(args: Any) -> int:
    command = args.model_command
    if command == "list":
        return invoke_action(args, "model.list", {})
    if command == "read":
        return invoke_action(args, "model.profile.read", {"profileId": args.profile_id})
    if command in {"readiness", "select"}:
        return invoke_action(
            args,
            f"model.{command}",
            {"agentId": args.agent_id, "role": args.role, "runOverride": args.run_override},
        )
    return invoke_action(
        args,
        "model.profile.apply",
        {
            "profileId": args.profile_id,
            "candidate": read_json_object(args.candidate),
            "expectedRevision": args.expected_revision,
        },
    )


def _dispatch_extension(args: Any) -> int:
    command = args.extension_command
    if command == "list":
        raw: dict[str, object] = {"kind": args.kind, "agentId": args.agent_id}
    elif command in {"get", "readiness"}:
        raw = {"kind": args.kind, "extensionId": args.extension_id}
    elif command == "preview":
        raw = {"kind": args.kind, "source": _source_input(args)}
    elif command == "install":
        raw = {
            "kind": args.kind,
            "source": _source_input(args),
            "expectedDigest": args.expected_digest,
            "expectedRevision": args.expected_revision,
        }
    elif command in {"enable", "disable"}:
        raw = {
            "kind": args.kind,
            "extensionId": args.extension_id,
            "agentId": args.agent_id,
            "expectedRevision": args.expected_revision,
        }
    else:
        raw = {
            "kind": args.kind,
            "extensionId": args.extension_id,
            "expectedRevision": args.expected_revision,
        }
    return invoke_action(args, f"extension.{command}", raw, confirmed=bool(getattr(args, "yes", False)))


def _cron_schedule(args: Any) -> dict[str, object]:
    if args.every_seconds is not None:
        return {"kind": "every", "everySeconds": args.every_seconds}
    if args.cron_expression is not None:
        value: dict[str, object] = {"kind": "cron", "cronExpression": args.cron_expression}
        if args.timezone:
            value["timezone"] = args.timezone
        return value
    return {"kind": "at", "atMs": args.at_ms}


def _dispatch_operations(args: Any) -> int:
    command = args.operations_command
    if command == "status":
        return invoke_action(args, "operations.overview", {})
    if command == "health":
        return invoke_action(args, "operations.health", {})
    if command == "tasks":
        return invoke_action(args, "operations.task.list", {"sessionId": args.session_id, "limit": args.limit})
    if command == "usage":
        return invoke_action(args, "operations.usage.read", {"limit": args.limit, "provider": args.provider})
    if command == "audit":
        return invoke_action(
            args,
            "operations.audit.list",
            {
                "limit": args.limit,
                "actorId": args.actor_id,
                "agentId": args.agent_id,
                "runId": args.run_id,
                "extensionId": args.extension_id,
                "actionId": args.action_id,
                "outcome": args.outcome,
            },
        )
    if command == "heartbeat":
        if args.heartbeat_command == "status":
            return invoke_action(args, "operations.heartbeat.status", {})
        return invoke_action(args, "operations.heartbeat.run", {"reason": args.reason})
    cron_command = args.cron_command
    if cron_command == "list":
        return invoke_action(
            args,
            "operations.cron.list",
            {"includeDisabled": args.include_disabled, "historyLimit": args.history_limit},
        )
    if cron_command == "create":
        raw = {
            "name": args.name,
            "agentId": args.agent_id,
            "userId": args.user_id,
            "message": args.message,
            "schedule": _cron_schedule(args),
            "deleteAfterRun": args.delete_after_run,
        }
    elif cron_command in {"enable", "disable"}:
        raw = {"jobId": args.job_id, "enabled": cron_command == "enable"}
        cron_command = "enable"
    elif cron_command == "run":
        raw = {"jobId": args.job_id, "force": args.force}
    else:
        raw = {"jobId": args.job_id}
    return invoke_action(args, f"operations.cron.{cron_command}", raw, confirmed=bool(args.yes))


def dispatch(args: Any) -> int:
    """Dispatch one parsed command and return its process exit code."""
    if args.command == "setup":
        return run_setup(args)
    if args.command == "node":
        if args.node_command == "run":
            run_node(args.node_root, host=args.host, port=args.port, access_token=args.access_token)
            return 0
        if args.service_command == "install":
            return install_node_service(args)
        return node_service_status(args)
    if args.command == "action":
        if args.action_command == "list":
            return action_catalog(args)
        try:
            raw = parse_json_object(args.input_json, label="--input-json")
        except ValueError as exc:
            print(f"Error: {exc}")
            return 2
        return invoke_action(args, args.action_id, raw, confirmed=args.yes)
    if args.command == "command":
        return invoke_action(
            args,
            "system.command.invoke",
            {
                "rawCommand": args.raw_command,
                "userId": args.user_id,
                "agentId": args.agent_id,
                "sessionId": args.session_id,
                "runId": args.run_id,
            },
        )
    try:
        if args.command == "config":
            return _dispatch_config(args)
        if args.command == "model":
            return _dispatch_model(args)
        if args.command == "extension":
            return _dispatch_extension(args)
        if args.command == "operations":
            return _dispatch_operations(args)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 2
    raise ValueError(f"unsupported command: {args.command}")
