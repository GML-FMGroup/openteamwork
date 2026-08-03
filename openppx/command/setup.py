"""First-run CLI flow over the same setup Actions used by every client."""

from __future__ import annotations

import getpass
import json
import socket
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

from openppx.actions import ActionContext
from openppx.client_api.contracts import ContractMapper
from openppx.runtime.client_api_client import ClientApiClient
from openppx.runtime.node_host import build_node_composition

from .transport import render_envelope


def _setup_context(request_id: str) -> ActionContext:
    """Build the trusted local context used only during offline first setup."""
    permissions = frozenset(
        {"setup.read", "setup.write", "secret.read", "secret.write", "model.read", "model.write", "run.start"}
    )
    return ActionContext(
        request_id=request_id,
        correlation_id=request_id,
        actor_id="principal:local-cli",
        capabilities=permissions,
        permissions=permissions,
    )


def _local_invoker(composition: Any) -> Callable[[str, dict[str, object]], dict[str, Any]]:
    """Return an Action invoker for an unbound local Node composition."""
    def invoke(action_id: str, raw_input: dict[str, object]) -> dict[str, Any]:
        request_id = f"req_setup_{uuid.uuid4().hex}"
        outcome = composition.control_plane.invoke(action_id, raw_input, _setup_context(request_id))
        return ContractMapper().from_outcome(
            outcome,
            request_id=request_id,
            correlation_id=request_id,
        ).model_dump(mode="json", by_alias=True)

    return invoke


def _remote_invoker(args: Any) -> Callable[[str, dict[str, object]], dict[str, Any]]:
    """Return an Action invoker for a running Node."""
    client = ClientApiClient(base_url=args.client_api_url, access_token=args.access_token)

    def invoke(action_id: str, raw_input: dict[str, object]) -> dict[str, Any]:
        request_id = f"req_setup_{uuid.uuid4().hex}"
        return client.invoke_action(action_id, raw_input, request_id=request_id)

    return invoke


def _result(envelope: dict[str, Any]) -> dict[str, Any] | None:
    value = envelope.get("result")
    return value if envelope.get("ok") is True and isinstance(value, dict) else None


def _provider(status: dict[str, Any], provider_id: str) -> dict[str, Any] | None:
    providers = status.get("providers")
    if not isinstance(providers, list):
        return None
    return next(
        (item for item in providers if isinstance(item, dict) and item.get("id") == provider_id),
        None,
    )


def _apply_input(args: Any, status: dict[str, Any], *, api_key: str | None) -> dict[str, object]:
    """Build one complete strict setup request from CLI values and server facts."""
    provider = _provider(status, args.provider)
    if provider is None:
        raise ValueError(f"Provider '{args.provider}' is not available on this OpenPPX Node")
    model = args.model or str(provider.get("defaultModel") or "").strip()
    if not model:
        raise ValueError(f"Provider '{args.provider}' has no default model; pass --model")
    recommended_workspace = str(status.get("recommendedWorkspace") or "").strip()
    workspace = str(Path(args.workspace or recommended_workspace).expanduser().resolve(strict=False))
    credential_ref = {"store": "system", "name": args.credential_name}
    profile_spec: dict[str, object] = {
        "provider": args.provider,
        "model": model,
        "executionLocation": args.execution_location,
        "capabilities": ["text", "tool_calling"],
    }
    secret: dict[str, object] | None = None
    if provider.get("credentialMode") == "api_key":
        profile_spec["credential"] = credential_ref
        if api_key:
            secret = {"ref": credential_ref, "value": api_key}
    revisions = status.get("revisions") if isinstance(status.get("revisions"), dict) else {}
    return {
        "request": {
            "node": {
                "apiVersion": "openppx.io/v1alpha1",
                "kind": "NodeConfig",
                "metadata": {"name": args.node_id},
                "spec": {
                    "displayName": args.node_name or socket.gethostname(),
                    "enabledAgents": [args.agent_id],
                    "clientApi": {
                        "listenHost": args.listen_host,
                        "port": args.listen_port,
                        "authentication": args.authentication,
                    },
                },
            },
            "agent": {
                "apiVersion": "openppx.io/v1alpha1",
                "kind": "AgentConfig",
                "metadata": {"name": args.agent_id},
                "spec": {
                    "displayName": args.agent_name,
                    "workspace": workspace,
                    "ownerPrincipalId": args.user_id,
                    "privilegeLevel": args.privilege_level,
                    "modelPolicy": {"defaultProfile": args.profile_id},
                },
            },
            "profile": {
                "apiVersion": "openppx.io/v1alpha1",
                "kind": "ModelProfile",
                "metadata": {"name": args.profile_id},
                "spec": profile_spec,
            },
            "secret": secret,
            "expectedRevisions": {
                "node": revisions.get("node"),
                "agent": revisions.get("agent"),
                "profile": revisions.get("profile"),
            },
        }
    }


def run_setup(args: Any) -> int:
    """Configure one Node and optionally verify the first real model turn."""
    composition = None
    if args.client_api_url:
        invoke = _remote_invoker(args)
    else:
        composition = build_node_composition(args.node_root)
        invoke = _local_invoker(composition)
    try:
        before_envelope = invoke("setup.status", {})
        before = _result(before_envelope)
        if before is None:
            return render_envelope(before_envelope, output_json=args.output_json)
        provider = _provider(before, args.provider)
        if provider is None:
            print(f"Error: Provider '{args.provider}' is not available on this OpenPPX Node")
            return 2
        api_key = args.api_key
        if provider.get("credentialMode") == "api_key" and not api_key:
            secret = invoke("secret.status", {"ref": {"store": "system", "name": args.credential_name}})
            secret_result = _result(secret)
            if secret_result is None or secret_result.get("state") != "available":
                if not sys.stdin.isatty():
                    print("Error: this provider requires an API key; pass --api-key in non-interactive mode")
                    return 2
                api_key = getpass.getpass(f"{provider.get('displayName', args.provider)} API key: ").strip()
        apply_envelope = invoke("setup.apply", _apply_input(args, before, api_key=api_key or None))
        applied = _result(apply_envelope)
        if applied is None:
            return render_envelope(apply_envelope, output_json=args.output_json)
        hello = None
        if not args.no_hello:
            hello_envelope = invoke(
                "setup.hello",
                {"agentId": args.agent_id, "userId": args.user_id, "text": args.hello},
            )
            hello = _result(hello_envelope)
            if hello is None:
                return render_envelope(hello_envelope, output_json=args.output_json)
        after_envelope = invoke("setup.status", {})
        after = _result(after_envelope)
        if after is None:
            return render_envelope(after_envelope, output_json=args.output_json)
    except (OSError, TimeoutError, ValueError) as exc:
        endpoint = args.client_api_url or str(args.node_root)
        print(f"Error: setup could not use OpenPPX Node at {endpoint}: {exc}")
        return 1
    finally:
        if composition is not None:
            composition.runtime_supervisor.close()
    payload = {"statusBefore": before, "apply": applied, "hello": hello, "statusAfter": after}
    if args.output_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Setup: {after.get('state', 'unknown')}")
        print(f"Node: {args.node_name or socket.gethostname()} ({args.node_id})")
        print(f"Agent: {args.agent_name} ({args.agent_id})")
        print(f"Model: {args.provider}/{args.model or provider.get('defaultModel')}")
        if hello is not None:
            print(f"Session: {hello.get('sessionId')}")
            print(str(hello.get("reply") or ""))
    return 0
