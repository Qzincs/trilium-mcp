"""Streamable HTTP entry point for the Trilium MCP server."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from mcp.server.fastmcp import FastMCP

from trilium_mcp.client import TriliumClient
from trilium_mcp.config import Settings
from trilium_mcp.tools.branches import register_branch_tools
from trilium_mcp.tools.notes import register_note_tools


def create_server(settings: Settings) -> FastMCP:
    """Build the MCP server and its read-only Trilium tools."""
    client = TriliumClient(settings)

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await client.aclose()

    mcp = FastMCP(
        "Trilium Notes",
        host=settings.mcp_host,
        port=settings.mcp_port,
        streamable_http_path="/mcp",
        lifespan=lifespan,
    )
    register_note_tools(mcp, client, settings)
    register_branch_tools(mcp, client)
    return mcp


def main() -> None:
    """Run the server with the Streamable HTTP transport."""
    create_server(Settings()).run(transport="streamable-http")


if __name__ == "__main__":
    main()
