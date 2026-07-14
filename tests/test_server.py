"""Basic checks for the MCP server skeleton."""

from trilium_mcp.config import Settings
from trilium_mcp.server import create_server


def test_server_uses_expected_identity_and_streamable_http_path() -> None:
    settings = Settings(etapi_url="https://notes.example.com/etapi", etapi_token="test-token")
    mcp = create_server(settings)

    assert mcp.name == "Trilium Notes"
    assert mcp.settings.host == "127.0.0.1"
    assert mcp.settings.port == 8000
    assert mcp.settings.streamable_http_path == "/mcp"
