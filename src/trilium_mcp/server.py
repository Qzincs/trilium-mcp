"""Streamable HTTP entry point for the Trilium MCP server."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.types import ASGIApp

from trilium_mcp.client import TriliumClient
from trilium_mcp.cloudflare_access import (
    CloudflareAccessMiddleware,
    CloudflareAccessVerifier,
)
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


def create_app(settings: Settings) -> ASGIApp:
    """Build the HTTP application and optionally protect it with Cloudflare Access."""
    app = create_server(settings).streamable_http_app()
    if settings.cf_access_enabled:
        app.add_middleware(
            CloudflareAccessMiddleware,
            verifier=CloudflareAccessVerifier(
                settings.cf_access_team_domain,
                settings.cf_access_aud,
                settings.cf_access_allowed_email,
            ),
        )
    return app


def main() -> None:
    """Run the server with the Streamable HTTP transport."""
    settings = Settings()
    uvicorn.run(create_app(settings), host=settings.mcp_host, port=settings.mcp_port)


if __name__ == "__main__":
    main()
