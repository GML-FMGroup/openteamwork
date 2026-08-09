"""Deterministic MCP Resources and binary-content fixture."""

from __future__ import annotations

import base64

from mcp.server.fastmcp import FastMCP, Image


server = FastMCP(name="openppx-content-mcp")

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@server.resource(
    "resource://openppx/allowed",
    name="allowed_notes",
    mime_type="text/plain",
)
def allowed_notes() -> str:
    """Return one explicitly allowlisted text Resource."""
    return "OpenPPX allowed resource"


@server.resource(
    "resource://openppx/blocked",
    name="blocked_notes",
    mime_type="text/plain",
)
def blocked_notes() -> str:
    """Return a Resource that must remain undiscoverable."""
    return "OpenPPX blocked resource"


@server.resource(
    "resource://openppx/binary",
    name="binary_notes",
    mime_type="image/png",
)
def binary_notes() -> bytes:
    """Return binary Resource content that must not enter model context."""
    return _PNG


@server.tool()
def render_pixel() -> Image:
    """Return one valid PNG as MCP ImageContent."""
    return Image(data=_PNG, format="png")


if __name__ == "__main__":
    server.run(transport="stdio")
