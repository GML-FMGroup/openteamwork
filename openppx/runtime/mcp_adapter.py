"""Runtime adapter from strict MCP resources to Google ADK toolsets."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from openppx.config import SecretBackendUnavailable, SecretNotFound, SecretStore
from openppx.core.mcp_registry import ManagedMcpToolset, build_mcp_toolsets, probe_mcp_toolsets
from openppx.extensions.mcp import McpSnapshot
from openppx.extensions.mcp_models import (
    McpEnvironmentValue,
    McpLiteralValue,
    McpRemoteTransport,
    McpSecretValue,
    McpStdioTransport,
    McpValueBinding,
)
from openppx.extensions.mcp_oauth import McpOAuthService
from openppx.permissions import PermissionAuditSink, ResolvedPermissionSnapshot, authorize_network_url


@dataclass(frozen=True, slots=True)
class McpRuntimeDiagnostic:
    """Non-sensitive reason why one MCP resource was omitted from a Runtime."""

    server_id: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class McpRuntimeBuild:
    """ADK toolsets and safe diagnostics produced for one immutable snapshot."""

    toolsets: tuple[ManagedMcpToolset, ...]
    diagnostics: tuple[McpRuntimeDiagnostic, ...]
    network_origins: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class McpProbeReport:
    """Connectivity results plus resource-resolution diagnostics."""

    results: tuple[dict[str, Any], ...]
    diagnostics: tuple[McpRuntimeDiagnostic, ...]


class _McpEnvironmentMissing(RuntimeError):
    """Raised without a value when a declared Runtime environment key is absent."""


class McpRuntimeAdapter:
    """Resolve protected bindings and construct real Google ADK MCP toolsets."""

    def __init__(self, secret_store: SecretStore, oauth_service: McpOAuthService | None = None) -> None:
        self.secret_store = secret_store
        self.oauth_service = oauth_service or McpOAuthService(secret_store)

    def build(
        self,
        snapshot: McpSnapshot,
        *,
        permission_snapshot: ResolvedPermissionSnapshot | None = None,
        permission_audit: PermissionAuditSink | None = None,
    ) -> McpRuntimeBuild:
        """Build toolsets without ever copying Secret values into persisted resources."""
        toolsets: list[ManagedMcpToolset] = []
        diagnostics: list[McpRuntimeDiagnostic] = []
        network_origins: list[tuple[str, str]] = []
        for entry in snapshot.entries:
            server_id = entry.record.metadata.name
            try:
                if permission_snapshot is not None and isinstance(entry.record.spec.transport, McpRemoteTransport):
                    authorize_network_url(
                        permission_snapshot,
                        entry.record.spec.transport.url,
                        method="POST",
                        actions=("connect", "read", "write", "upload"),
                        audit=permission_audit,
                    )
                raw = self._runtime_config(server_id, entry.record.spec.transport)
            except PermissionError:
                diagnostics.append(
                    McpRuntimeDiagnostic(
                        server_id=server_id,
                        code="permission_denied",
                        message="Remote MCP endpoint is not allowed by the Agent network policy.",
                    )
                )
                continue
            except SecretNotFound:
                diagnostics.append(
                    McpRuntimeDiagnostic(
                        server_id=server_id,
                        code="authentication_missing",
                        message="Required MCP authentication is not configured.",
                    )
                )
                continue
            except SecretBackendUnavailable:
                diagnostics.append(
                    McpRuntimeDiagnostic(
                        server_id=server_id,
                        code="authentication_unavailable",
                        message="The protected credential service is unavailable.",
                    )
                )
                continue
            except _McpEnvironmentMissing:
                diagnostics.append(
                    McpRuntimeDiagnostic(
                        server_id=server_id,
                        code="environment_missing",
                        message="A required MCP environment variable is not configured.",
                    )
                )
                continue

            policy = entry.record.spec.policy
            raw.update(
                {
                    "toolNamePrefix": policy.resolved_prefix(server_id),
                    "requireConfirmation": policy.require_confirmation,
                    "runtimeHeaders": dict(policy.runtime_headers),
                    "progressEvents": policy.progress_events,
                    "longTaskProxy": policy.long_task_proxy,
                    "inlineBudgetMs": policy.inline_budget_ms,
                }
            )
            if policy.tool_filter:
                raw["toolFilter"] = list(policy.tool_filter)
            if policy.disabled_tools:
                raw["disabledTools"] = list(policy.disabled_tools)
            if policy.job_protocol is not None:
                raw["jobProtocol"] = policy.job_protocol.model_dump(mode="json", by_alias=True)
            built = build_mcp_toolsets({server_id: raw}, log_registered=False)
            toolsets.extend(built)
            if isinstance(entry.record.spec.transport, McpRemoteTransport) and built:
                network_origins.append(
                    (policy.resolved_prefix(server_id), entry.record.spec.transport.url)
                )
        return McpRuntimeBuild(
            toolsets=tuple(toolsets),
            diagnostics=tuple(diagnostics),
            network_origins=tuple(network_origins),
        )

    async def probe(
        self,
        snapshot: McpSnapshot,
        *,
        timeout_seconds: float = 5.0,
        retry_attempts: int = 1,
    ) -> McpProbeReport:
        """Test the currently resolved MCP resources and always close sessions."""
        build = self.build(snapshot)
        try:
            results = await probe_mcp_toolsets(
                list(build.toolsets),
                timeout_seconds=timeout_seconds,
                retry_attempts=retry_attempts,
            )
            return McpProbeReport(results=tuple(results), diagnostics=build.diagnostics)
        finally:
            for toolset in build.toolsets:
                await toolset.close()

    def _runtime_config(
        self,
        server_id: str,
        transport: McpStdioTransport | McpRemoteTransport,
    ) -> dict[str, Any]:
        """Convert one strict transport into the mature ADK builder contract."""
        if isinstance(transport, McpStdioTransport):
            return {
                "command": transport.command,
                "args": list(transport.args),
                "cwd": transport.cwd,
                "env": {
                    name: self._resolve_binding(binding)
                    for name, binding in transport.environment.items()
                },
            }
        query = {
            name: self._resolve_binding(binding)
            for name, binding in transport.query.items()
        }
        config = {
            "url": f"{transport.url}?{urlencode(query)}" if query else transport.url,
            "transport": "sse" if transport.type == "sse" else "http",
            "headers": {
                name: self._resolve_binding(binding)
                for name, binding in transport.headers.items()
            },
        }
        if transport.auth == "oauth":
            config["httpxClientFactory"] = self.oauth_service.runtime_httpx_factory(
                server_id,
                transport.url,
            )
        return config

    def _resolve_binding(self, binding: McpValueBinding) -> str:
        """Reveal a Secret only at the final in-memory connection boundary."""
        if isinstance(binding, McpLiteralValue):
            return binding.value
        if isinstance(binding, McpEnvironmentValue):
            value = os.environ.get(binding.name)
            if value is None:
                raise _McpEnvironmentMissing(binding.name)
            return f"{binding.prefix}{value}{binding.suffix}"
        if isinstance(binding, McpSecretValue):
            value = self.secret_store.resolve(binding.secret_ref).reveal()
            return f"{binding.prefix}{value}{binding.suffix}"
        raise TypeError("Unsupported MCP value binding")


__all__ = [
    "McpProbeReport",
    "McpRuntimeAdapter",
    "McpRuntimeBuild",
    "McpRuntimeDiagnostic",
]
