from pathlib import Path

import pytest
from pydantic import ValidationError

from trilium_mcp.config import Settings


def test_settings_load_dotenv_file(tmp_path: Path) -> None:
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text(
        "TRILIUM_ETAPI_URL=https://notes.example.com/etapi\n"
        "TRILIUM_ETAPI_TOKEN=dotenv-token\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=dotenv_file)

    assert settings.etapi_url == "https://notes.example.com/etapi"
    assert settings.etapi_token.get_secret_value() == "dotenv-token"


def test_production_requires_cloudflare_access_settings() -> None:
    with pytest.raises(ValidationError, match="CF_ACCESS_TEAM_DOMAIN"):
        Settings(
            etapi_url="https://notes.example.com/etapi",
            etapi_token="test-token",
            environment="production",
        )
