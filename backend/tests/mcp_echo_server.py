"""Tiny stdio MCP server used by the MCP integration tests (mcp 2.x API)."""

from mcp.server import MCPServer

mcp = MCPServer("echo")


@mcp.tool()
def echo(text: str) -> str:
    """Echo the input back."""
    return f"echo: {text}"


@mcp.tool()
def add(a: int, b: int) -> str:
    """Add two integers."""
    return str(a + b)


if __name__ == "__main__":
    mcp.run()
