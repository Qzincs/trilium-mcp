"""Environment-backed server configuration."""

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration that remains on the MCP server."""

    model_config = SettingsConfigDict(
        extra="ignore",
        populate_by_name=True,
        env_file=".env",
        env_file_encoding="utf-8",
    )

    etapi_url: str = Field(validation_alias="TRILIUM_ETAPI_URL")
    etapi_token: SecretStr = Field(validation_alias="TRILIUM_ETAPI_TOKEN")
    mcp_host: str = Field(default="127.0.0.1", validation_alias="MCP_HOST")
    mcp_port: int = Field(default=8000, validation_alias="MCP_PORT")
    request_timeout: float = Field(default=30.0, validation_alias="TRILIUM_REQUEST_TIMEOUT")
    default_search_limit: int = Field(
        default=10, validation_alias="TRILIUM_DEFAULT_SEARCH_LIMIT", ge=1
    )
    max_search_limit: int = Field(default=50, validation_alias="TRILIUM_MAX_SEARCH_LIMIT", ge=1)
    max_content_chars: int = Field(
        default=100_000, validation_alias="TRILIUM_MAX_CONTENT_CHARS", ge=1
    )

    @field_validator("etapi_url")
    @classmethod
    def normalize_etapi_url(cls, value: str) -> str:
        value = value.rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("TRILIUM_ETAPI_URL must start with http:// or https://")
        return value
