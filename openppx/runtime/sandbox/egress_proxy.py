"""Small revision-aware HTTP CONNECT proxy for proxy-only Agent sandboxes.

Run this service in a trusted container attached to both the OpenPPX internal
network and an external network. Agent task containers attach only to the
internal network, so non-proxy traffic has no route to the internet.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import re
import selectors
import socket
import socketserver
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from openppx.product import PRODUCT

from .egress_policy import (
    classify_proxy_visibility,
    proxy_policy_allows,
    proxy_policy_credential_matches,
)


_MAX_HEADER_BYTES = 64 * 1024
_REVISION_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_LOGGER = logging.getLogger("openppx.egress_proxy")


class EgressProxyServer(socketserver.ThreadingTCPServer):
    """Threaded proxy server with an immutable policy directory."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], policy_directory: Path) -> None:
        self.policy_directory = policy_directory.expanduser().resolve(strict=True)
        super().__init__(address, EgressProxyHandler)

    def load_policy(self, revision: str) -> dict[str, Any]:
        """Load one revision policy without accepting path-like identifiers."""

        if _REVISION_RE.fullmatch(revision) is None:
            raise PermissionError("invalid permission revision")
        path = self.policy_directory / f"{revision.replace(':', '-')}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("permissionRevision") != revision:
            raise PermissionError("permission policy revision mismatch")
        return payload


class EgressProxyHandler(socketserver.StreamRequestHandler):
    """Authorize and forward one HTTP or HTTPS CONNECT request."""

    server: EgressProxyServer

    def handle(self) -> None:
        try:
            request_line, headers = self._read_headers()
            method, target, version = request_line.split(" ", 2)
            revision, credential = _identity_from_proxy_authorization(
                headers.get("proxy-authorization", "")
            )
            policy = self.server.load_policy(revision)
            if not proxy_policy_credential_matches(policy, credential):
                raise PermissionError("proxy revision credential is invalid")
            if method.upper() == "CONNECT":
                host, port = _connect_target(target)
                self._authorize_and_tunnel(policy, host=host, port=port)
                return
            self._authorize_and_forward_http(
                policy,
                method=method.upper(),
                target=target,
                version=version,
                headers=headers,
            )
        except (FileNotFoundError, json.JSONDecodeError):
            self._error(403, "Forbidden", "permission policy is unavailable")
        except (PermissionError, ValueError) as exc:
            self._error(403, "Forbidden", str(exc))
        except Exception:
            self._error(502, "Bad Gateway", "proxy request failed")

    def _read_headers(self) -> tuple[str, dict[str, str]]:
        total = 0
        first = self.rfile.readline(_MAX_HEADER_BYTES + 1)
        total += len(first)
        if not first or total > _MAX_HEADER_BYTES:
            raise ValueError("invalid or oversized proxy request")
        request_line = first.decode("iso-8859-1").strip()
        headers: dict[str, str] = {}
        while True:
            line = self.rfile.readline(_MAX_HEADER_BYTES + 1)
            total += len(line)
            if total > _MAX_HEADER_BYTES:
                raise ValueError("proxy headers are too large")
            if line in {b"\r\n", b"\n", b""}:
                break
            name, separator, value = line.decode("iso-8859-1").partition(":")
            if not separator:
                raise ValueError("malformed proxy header")
            headers[name.strip().lower()] = value.strip()
        return request_line, headers

    def _authorize_and_tunnel(self, policy: dict[str, Any], *, host: str, port: int) -> None:
        addresses = _resolve_publication(host, port)
        visibility = classify_proxy_visibility(host, addresses)
        allowed, reason = proxy_policy_allows(
            policy,
            scheme="https",
            host=host,
            port=port,
            resolved_ips=addresses,
            visibility=visibility,
            method="CONNECT",
        )
        _audit_proxy_decision(
            policy,
            method="CONNECT",
            scheme="https",
            port=port,
            visibility=visibility,
            outcome="allow" if allowed else "deny",
            reason=reason,
        )
        if not allowed:
            raise PermissionError(reason)
        upstream = socket.create_connection((addresses[0], port), timeout=20)
        try:
            self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self.wfile.flush()
            _relay_bidirectional(self.connection, upstream)
        finally:
            upstream.close()

    def _authorize_and_forward_http(
        self,
        policy: dict[str, Any],
        *,
        method: str,
        target: str,
        version: str,
        headers: dict[str, str],
    ) -> None:
        parsed = urlsplit(target)
        if parsed.scheme != "http" or not parsed.hostname:
            raise PermissionError("plain HTTP proxy requests require an absolute http URL")
        port = parsed.port or 80
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        addresses = _resolve_publication(host, port)
        visibility = classify_proxy_visibility(host, addresses)
        allowed, reason = proxy_policy_allows(
            policy,
            scheme="http",
            host=host,
            port=port,
            resolved_ips=addresses,
            visibility=visibility,
            method=method,
        )
        _audit_proxy_decision(
            policy,
            method=method,
            scheme="http",
            port=port,
            visibility=visibility,
            outcome="allow" if allowed else "deny",
            reason=reason,
        )
        if not allowed:
            raise PermissionError(reason)
        upstream = socket.create_connection((addresses[0], port), timeout=20)
        try:
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
            upstream.sendall(f"{method} {path} {version}\r\n".encode("ascii"))
            for name, value in headers.items():
                if name in {"proxy-authorization", "proxy-connection", "connection"}:
                    continue
                upstream.sendall(f"{name}: {value}\r\n".encode("iso-8859-1"))
            upstream.sendall(b"connection: close\r\n\r\n")
            content_length = int(headers.get("content-length", "0") or "0")
            if content_length < 0 or content_length > 32 * 1024 * 1024:
                raise ValueError("request body is too large")
            remaining = content_length
            while remaining:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    raise ValueError("request body ended early")
                upstream.sendall(chunk)
                remaining -= len(chunk)
            while True:
                chunk = upstream.recv(65536)
                if not chunk:
                    break
                self.connection.sendall(chunk)
        finally:
            upstream.close()

    def _error(self, status: int, reason: str, detail: str) -> None:
        body = json.dumps({"error": reason.lower().replace(" ", "_"), "detail": detail[:256]}).encode("utf-8")
        try:
            self.connection.sendall(
                f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii")
                + body
            )
        except OSError:
            pass


def _identity_from_proxy_authorization(value: str) -> tuple[str, str]:
    scheme, separator, token = value.partition(" ")
    if not separator or scheme.lower() != "basic":
        raise PermissionError("proxy revision identity is required")
    try:
        decoded = base64.b64decode(token, validate=True).decode("utf-8")
    except Exception as exc:
        raise PermissionError("proxy revision identity is invalid") from exc
    identity, separator, credential = decoded.partition(":")
    revision = identity.replace("sha256-", "sha256:", 1)
    if not separator or _REVISION_RE.fullmatch(revision) is None:
        raise PermissionError("proxy revision identity is invalid")
    if not credential:
        raise PermissionError("proxy revision credential is invalid")
    return revision, credential


def _audit_proxy_decision(
    policy: dict[str, Any],
    *,
    method: str,
    scheme: str,
    port: int,
    visibility: str,
    outcome: str,
    reason: str,
) -> None:
    """Emit one destination-redacted decision for the trusted proxy log."""

    _LOGGER.info(
        json.dumps(
            {
                "event": "openppx_egress_decision",
                "agentId": policy.get("agentId"),
                "permissionRevision": policy.get("permissionRevision"),
                "method": method,
                "scheme": scheme,
                "port": port,
                "visibility": visibility,
                "outcome": outcome,
                "reasonCode": reason,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _connect_target(value: str) -> tuple[str, int]:
    host, separator, raw_port = value.rpartition(":")
    if not separator or not host:
        raise ValueError("CONNECT target must include host:port")
    normalized = host.strip("[]").rstrip(".").encode("idna").decode("ascii").lower()
    port = int(raw_port)
    if port < 1 or port > 65535:
        raise ValueError("CONNECT port is invalid")
    return normalized, port


def _resolve_publication(host: str, port: int) -> tuple[str, ...]:
    values = sorted(
        {
            str(info[4][0])
            for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            if isinstance(info[4], tuple) and info[4]
        }
    )
    if not values:
        raise PermissionError("DNS returned no addresses")
    return tuple(values)


def _relay_bidirectional(client: socket.socket, upstream: socket.socket) -> None:
    selector = selectors.DefaultSelector()
    selector.register(client, selectors.EVENT_READ, upstream)
    selector.register(upstream, selectors.EVENT_READ, client)
    try:
        while True:
            events = selector.select(timeout=60)
            if not events:
                return
            for key, _mask in events:
                data = key.fileobj.recv(65536)
                if not data:
                    return
                key.data.sendall(data)
    finally:
        selector.close()


def main() -> None:
    """Run the trusted egress proxy service."""

    parser = argparse.ArgumentParser(description=f"{PRODUCT.display_name} revision-aware egress proxy")
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3128)
    parser.add_argument("--policy-directory", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with EgressProxyServer((args.listen, args.port), args.policy_directory) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
