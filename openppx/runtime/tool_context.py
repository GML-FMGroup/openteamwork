"""Per-request tool routing context."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator


_ROUTE: ContextVar[str | None] = ContextVar("route", default=None)
_SCOPE_ID: ContextVar[str | None] = ContextVar("scope_id", default=None)


def get_route() -> tuple[str | None, str | None]:
    """Return the current transport route and its opaque scope identifier."""

    return _ROUTE.get(), _SCOPE_ID.get()


@contextmanager
def route_context(route: str, scope_id: str) -> Iterator[None]:
    """Bind a Node-owned transport route while tools execute."""

    route_token: Token[str | None] = _ROUTE.set(route)
    scope_token: Token[str | None] = _SCOPE_ID.set(scope_id)
    try:
        yield
    finally:
        _ROUTE.reset(route_token)
        _SCOPE_ID.reset(scope_token)
