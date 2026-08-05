"""Small stdio MCP server bundled with the portable Plugin example."""

from mcp.server.fastmcp import FastMCP


server = FastMCP(name="openppx-workbench")


@server.tool()
def echo_context(token: str) -> dict[str, str]:
    """Return a verification token unchanged."""
    return {"status": "ok", "token": token}


if __name__ == "__main__":
    server.run(transport="stdio")
