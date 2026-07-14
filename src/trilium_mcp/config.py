"""Environment-backed server configuration."""

from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
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
    environment: Literal["development", "production"] = Field(
        default="development", validation_alias="MCP_ENVIRONMENT"
    )
    cf_access_team_domain: str | None = Field(
        default=None, validation_alias="CF_ACCESS_TEAM_DOMAIN"
    )
    cf_access_aud: str | None = Field(default=None, validation_alias="CF_ACCESS_AUD")
    cf_access_allowed_email: str | None = Field(
        default=None, validation_alias="CF_ACCESS_ALLOWED_EMAIL"
    )

    @field_validator("etapi_url")
    @classmethod
    def normalize_etapi_url(cls, value: str) -> str:
        value = value.rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("TRILIUM_ETAPI_URL must start with http:// or https://")
        return value

    @field_validator("cf_access_team_domain")
    @classmethod
    def normalize_cf_access_team_domain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.rstrip("/")
        if not value.startswith("https://"):
            raise ValueError("CF_ACCESS_TEAM_DOMAIN must start with https://")
        return value

    @model_validator(mode="after")
    def validate_cf_access_settings(self) -> "Settings":
        has_domain = self.cf_access_team_domain is not None
        has_aud = self.cf_access_aud is not None
        if has_domain != has_aud:
            raise ValueError("CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD must be set together")
        if self.cf_access_allowed_email and not has_domain:
            raise ValueError("CF_ACCESS_ALLOWED_EMAIL requires Cloudflare Access settings")
        if self.environment == "production" and not has_domain:
            raise ValueError("production requires CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD")
        return self

    @property
    def cf_access_enabled(self) -> bool:
        return self.cf_access_team_domain is not None
