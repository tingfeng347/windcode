from mcp.server.fastmcp import FastMCP

server = FastMCP("complete-plugin", instructions="Complete plugin MCP instructions")


@server.tool()
def echo(text: str) -> str:
    """Echo text from the complete plugin."""
    return text


if __name__ == "__main__":
    server.run(transport="stdio")
