"""Proxy-only sandbox and revision-bound egress policy tests."""

from __future__ import annotations

import json
import base64
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import pytest
from pydantic import ValidationError

from openppx.config import AgentConfig, NodeConfig
from openppx.permissions import compile_permission_snapshot
from openppx.runtime.sandbox import (
    SandboxValidationError,
    build_workspace_docker_sandbox,
    derive_sandbox_permission_profile,
    verify_docker_internal_network,
)
from openppx.runtime.sandbox.egress_policy import (
    classify_proxy_visibility,
    load_egress_proxy_credential,
    proxy_policy_allows,
    proxy_policy_credential_matches,
    proxy_policy_payload,
    write_egress_proxy_policy,
)
from openppx.runtime.sandbox.egress_proxy import EgressProxyServer


def _snapshot(workspace: Path, policy_directory: Path):
    node = NodeConfig.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "NodeConfig",
            "metadata": {"name": "node"},
            "spec": {
                "displayName": "Node",
                "enabledAgents": ["worker"],
                "permissions": {
                    "codeEgressProxy": {
                        "url": "http://openppx-egress-proxy:3128",
                        "dockerNetwork": "openppx-egress-internal",
                        "policyDirectory": str(policy_directory),
                    }
                },
            },
        }
    )
    agent = AgentConfig.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "AgentConfig",
            "metadata": {"name": "worker"},
            "spec": {
                "displayName": "Worker",
                "workspace": str(workspace),
                "ownerPrincipalId": "local:owner",
                "privilegeLevel": "medium",
                "permissions": {
                    "rules": [
                        {
                            "ruleId": "deny-blocked-domain",
                            "effect": "deny",
                            "object": "network",
                            "actions": ["connect", "read", "write", "upload"],
                            "selector": {"kind": "network", "domains": ["*.blocked.example"]},
                        }
                    ]
                },
            },
        }
    )
    return compile_permission_snapshot(node=node, agent=agent)


def test_proxy_only_docker_spec_uses_internal_network_and_revision_identity(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    snapshot = _snapshot(workspace, tmp_path / "policies")
    write_egress_proxy_policy(snapshot, policy_directory=tmp_path / "policies")
    profile = derive_sandbox_permission_profile(snapshot, workspace_root=workspace)

    sandbox = build_workspace_docker_sandbox(
        command_argv=["python", "task.py"],
        workspace=workspace,
        cwd=workspace,
        timeout_seconds=30,
        permission_profile=profile,
        verify_proxy_network=False,
    )

    argv = sandbox.argv
    proxy_identity = snapshot.revision.replace(":", "-")
    assert argv[argv.index("--network") + 1] == "openppx-egress-internal"
    assert any(
        value.startswith(f"HTTPS_PROXY=http://{proxy_identity}:")
        and value.endswith("@openppx-egress-proxy:3128")
        for value in argv
    )
    assert "OPENPPX_PERMISSION_REVISION=" + snapshot.revision in argv


def test_proxy_policy_is_atomic_revision_bound_and_matches_domain_and_private_denies(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    snapshot = _snapshot(workspace, tmp_path / "policies")

    path = write_egress_proxy_policy(snapshot, policy_directory=tmp_path / "policies")
    policy = json.loads(path.read_text(encoding="utf-8"))

    assert policy["permissionRevision"] == snapshot.revision
    credential = load_egress_proxy_credential(
        policy_directory=tmp_path / "policies",
        permission_revision=snapshot.revision,
    )
    assert proxy_policy_credential_matches(policy, credential)
    assert not proxy_policy_credential_matches(policy, "wrong-credential")
    assert credential not in json.dumps(policy)
    allowed, _ = proxy_policy_allows(
        policy,
        scheme="https",
        host="docs.example.com",
        port=443,
        resolved_ips=("93.184.216.34",),
        visibility="public",
        method="CONNECT",
    )
    blocked, reason = proxy_policy_allows(
        policy,
        scheme="https",
        host="api.blocked.example",
        port=443,
        resolved_ips=("93.184.216.34",),
        visibility="public",
        method="CONNECT",
    )
    private, _ = proxy_policy_allows(
        policy,
        scheme="http",
        host="service.internal",
        port=80,
        resolved_ips=("10.0.0.1",),
        visibility=classify_proxy_visibility("service.internal", ("10.0.0.1",)),
        method="GET",
    )

    assert allowed is True
    assert blocked is False and reason == "explicit_deny:connect"
    assert private is False


def test_proxy_policy_fails_closed_for_unknown_constraint_fields() -> None:
    policy = {
        "defaults": {"connect": "deny", "read": "allow"},
        "rules": [
            {
                "effect": "allow",
                "action": "connect",
                "selector": {"kind": "all"},
                "constraints": {"kind": "network", "unknownFutureLimit": 1},
            }
        ],
    }

    allowed, reason = proxy_policy_allows(
        policy,
        scheme="http",
        host="docs.example.com",
        port=80,
        resolved_ips=("93.184.216.34",),
        visibility="public",
        method="GET",
    )

    assert allowed is False
    assert reason == "default_deny:connect"


def test_proxy_only_network_verification_requires_docker_internal_flag() -> None:
    success = mock.Mock(returncode=0, stdout="true\n", stderr="")
    with mock.patch("openppx.runtime.sandbox.proxy_network.subprocess.run", return_value=success):
        verify_docker_internal_network(docker_bin="docker", network_name="internal")

    failure = mock.Mock(returncode=0, stdout="false\n", stderr="")
    with mock.patch("openppx.runtime.sandbox.proxy_network.subprocess.run", return_value=failure):
        with pytest.raises(SandboxValidationError, match="--internal"):
            verify_docker_internal_network(docker_bin="docker", network_name="bridge")


def test_code_egress_proxy_policy_directory_must_be_absolute() -> None:
    raw = {
        "apiVersion": "openppx.io/v1alpha1",
        "kind": "NodeConfig",
        "metadata": {"name": "node"},
        "spec": {
            "displayName": "Node",
            "permissions": {
                "codeEgressProxy": {
                    "url": "http://proxy:3128",
                    "dockerNetwork": "internal",
                    "policyDirectory": "relative/policies",
                }
            },
        },
    }

    with pytest.raises(ValidationError):
        NodeConfig.model_validate(raw)


def test_proxy_policy_payload_contains_no_workspace_or_source_revisions(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    payload = proxy_policy_payload(
        _snapshot(workspace, tmp_path / "policies"),
        credential_digest="0" * 64,
    )
    rendered = json.dumps(payload)

    assert str(workspace) not in rendered
    assert "sourceRevisions" not in rendered


def test_proxy_policy_directory_cannot_be_inside_agent_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    snapshot = _snapshot(workspace, workspace / "policies")

    with pytest.raises(PermissionError, match="Agent Workspace"):
        write_egress_proxy_policy(snapshot, policy_directory=workspace / "policies")


def test_bundled_proxy_forwards_an_authorized_revision_bound_http_request(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    snapshot = _snapshot(workspace, tmp_path / "policies")
    policy_path = write_egress_proxy_policy(snapshot, policy_directory=tmp_path / "policies")
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    for action in ("connect", "read", "private_access"):
        policy["rules"].append(
            {
                "ruleId": f"test-allow-loopback-{action}",
                "effect": "allow",
                "action": action,
                "selector": {"kind": "network", "cidrs": ["127.0.0.0/8"]},
            }
        )
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    class TargetHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            body = b"proxy-ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    target = None
    try:
        target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)
        proxy = EgressProxyServer(("127.0.0.1", 0), tmp_path / "policies")
    except PermissionError:
        if target is not None:
            target.server_close()
        pytest.skip("test sandbox does not allow binding loopback sockets")
    target_thread = threading.Thread(target=target.serve_forever, daemon=True)
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    target_thread.start()
    proxy_thread.start()
    try:
        credential = load_egress_proxy_credential(
            policy_directory=tmp_path / "policies",
            permission_revision=snapshot.revision,
        )
        identity = snapshot.revision.replace(":", "-") + ":" + credential
        authorization = base64.b64encode(identity.encode("utf-8")).decode("ascii")
        with socket.create_connection(proxy.server_address, timeout=5) as client:
            client.sendall(
                (
                    f"GET http://127.0.0.1:{target.server_port}/ HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{target.server_port}\r\n"
                    f"Proxy-Authorization: Basic {authorization}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii")
            )
            response = b""
            while chunk := client.recv(65536):
                response += chunk
    finally:
        proxy.shutdown()
        target.shutdown()
        proxy.server_close()
        target.server_close()

    assert b"200 OK" in response
    assert response.endswith(b"proxy-ok")
