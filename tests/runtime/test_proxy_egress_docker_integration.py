"""Opt-in end-to-end Docker test for proxy-only arbitrary-code egress."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

from openppx.config import AgentConfig, NodeConfig
from openppx.permissions import ResolvedPermissionSnapshot, compile_permission_snapshot
from openppx.runtime.sandbox.egress_policy import (
    load_egress_proxy_credential,
    write_egress_proxy_policy,
)


pytestmark = pytest.mark.skipif(
    os.getenv("OPENPPX_RUN_DOCKER_EGRESS_TESTS", "").strip().lower() not in {"1", "true", "yes", "on"},
    reason="set OPENPPX_RUN_DOCKER_EGRESS_TESTS=1 to run Docker egress integration",
)


def _run(*argv: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        check=check,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_task_container_has_proxy_egress_but_no_direct_route(tmp_path: Path) -> None:
    suffix = uuid.uuid4().hex[:12]
    internal_network = f"openppx-egress-internal-{suffix}"
    external_network = f"openppx-egress-external-{suffix}"
    proxy_name = f"openppx-egress-proxy-{suffix}"
    target_name = f"openppx-egress-target-{suffix}"
    proxy_image = os.getenv("OPENPPX_EGRESS_PROXY_IMAGE", "openppx-egress-proxy:dev")
    sandbox_image = os.getenv("OPENPPX_SANDBOX_IMAGE", "openppx-sandbox:dev")
    workspace = tmp_path / "workspace"
    policies = tmp_path / "policies"
    workspace.mkdir()
    snapshot = _snapshot(
        workspace=workspace,
        policy_directory=policies,
        proxy_name=proxy_name,
        internal_network=internal_network,
        target_name=target_name,
    )
    write_egress_proxy_policy(snapshot, policy_directory=policies)
    credential = load_egress_proxy_credential(
        policy_directory=policies,
        permission_revision=snapshot.revision,
    )
    identity = snapshot.revision.replace(":", "-")

    try:
        _run("docker", "network", "create", "--internal", internal_network)
        _run("docker", "network", "create", external_network)
        _run(
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            target_name,
            "--network",
            external_network,
            sandbox_image,
            "python",
            "-m",
            "http.server",
            "8000",
        )
        _run(
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            proxy_name,
            "--network",
            external_network,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--mount",
            f"type=bind,src={policies},dst=/policies,readonly",
            proxy_image,
            "--policy-directory",
            "/policies",
        )
        _run("docker", "network", "connect", internal_network, proxy_name)
        inspected = json.loads(_run("docker", "inspect", target_name).stdout)
        target_ip = inspected[0]["NetworkSettings"]["Networks"][external_network]["IPAddress"]
        proxy_url = f"http://{identity}:{credential}@{proxy_name}:3128"
        script = (
            "import socket, urllib.request; "
            f"print(urllib.request.urlopen('http://{target_name}:8000', timeout=10).status); "
            "blocked=False; s=socket.socket(); s.settimeout(2); "
            f"\ntry: s.connect(('{target_ip}', 8000))\n"
            "except OSError: blocked=True\n"
            "finally: s.close()\n"
            "print('DIRECT_BLOCKED=' + str(blocked))"
        )
        result = _run(
            "docker",
            "run",
            "--rm",
            "--network",
            internal_network,
            "--env",
            f"http_proxy={proxy_url}",
            "--env",
            f"HTTP_PROXY={proxy_url}",
            sandbox_image,
            "python",
            "-c",
            script,
        )

        assert "200" in result.stdout
        assert "DIRECT_BLOCKED=True" in result.stdout
    finally:
        _run("docker", "rm", "-f", proxy_name, target_name, check=False)
        _run("docker", "network", "rm", internal_network, external_network, check=False)


def _snapshot(
    *,
    workspace: Path,
    policy_directory: Path,
    proxy_name: str,
    internal_network: str,
    target_name: str,
) -> ResolvedPermissionSnapshot:
    node = NodeConfig.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "NodeConfig",
            "metadata": {"name": "node"},
            "spec": {
                "displayName": "Node",
                "permissions": {
                    "codeEgressProxy": {
                        "url": f"http://{proxy_name}:3128",
                        "dockerNetwork": internal_network,
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
                            "ruleId": "test-allow-private-target",
                            "effect": "allow",
                            "object": "network",
                            "actions": ["private_access"],
                            "selector": {"kind": "network", "domains": [target_name]},
                        }
                    ]
                },
            },
        }
    )
    return compile_permission_snapshot(node=node, agent=agent)
