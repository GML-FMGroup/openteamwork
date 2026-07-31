"""Authentication policy for the OpenPPX Client API."""

from __future__ import annotations

import ipaddress
import os
import secrets
from dataclasses import dataclass


CLIENT_API_TOKEN_ENV = "OPENPPX_CLIENT_API_TOKEN"


def is_loopback_bind_host(host: str) -> bool:
    """Return whether a bind host is restricted to the local machine."""

    normalized = str(host or "").strip().lower().strip("[]")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def resolve_client_api_access_token(explicit_token: str | None = None) -> str:
    """Resolve and validate one Client API bearer token."""

    token = explicit_token if explicit_token is not None else os.getenv(CLIENT_API_TOKEN_ENV, "")
    normalized = str(token or "").strip()
    if any(character.isspace() for character in normalized):
        raise ValueError(f"{CLIENT_API_TOKEN_ENV} must not contain whitespace.")
    return normalized


def validate_client_api_bind(*, host: str, access_token: str) -> None:
    """Reject unauthenticated network binds while allowing loopback development."""

    if not is_loopback_bind_host(host) and not access_token:
        raise ValueError(
            f"Refusing non-loopback Client API bind '{host}' without {CLIENT_API_TOKEN_ENV}."
        )


@dataclass(frozen=True)
class ClientApiAuthPolicy:
    """Validate bearer credentials using constant-time comparison."""

    access_token: str = ""

    @property
    def required(self) -> bool:
        """Return whether protected routes require a credential."""

        return bool(self.access_token)

    def authorizes(self, authorization_header: str | None) -> bool:
        """Return whether one Authorization header satisfies this policy."""

        if not self.required:
            return True
        scheme, separator, candidate = str(authorization_header or "").partition(" ")
        if not separator or scheme.lower() != "bearer" or not candidate:
            return False
        return secrets.compare_digest(candidate, self.access_token)
