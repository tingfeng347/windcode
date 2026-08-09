from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP


def create_server(*, host: str = "127.0.0.1", port: int = 8000) -> FastMCP[None]:
    server: FastMCP[None] = FastMCP(
        "windcode-contract",
        instructions="Contract server instructions",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        json_response=True,
    )

    @server.tool()
    def echo(text: str) -> str:
        """Echo a string."""
        return text

    @server.tool()
    def add(left: int, right: int) -> int:
        """Add two integers."""
        return left + right

    @server.resource("memo://example")
    def memo() -> str:
        return "resource content"

    @server.prompt()
    def review(topic: str = "code") -> str:
        """Create a review prompt."""
        return f"Review {topic}"

    _ = (echo, add, memo, review)
    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args()
    create_server(port=arguments.port).run(transport=arguments.transport)


if __name__ == "__main__":
    main()
