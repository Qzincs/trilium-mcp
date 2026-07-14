"""Cloudflare Access JWT verification for the HTTP MCP endpoint."""

import asyncio
import logging
from typing import Any

import httpx
import jwt
from jwt import InvalidTokenError, PyJWK
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)


class CloudflareAccessAuthenticationError(Exception):
    """The Cloudflare Access assertion is missing or invalid."""


class CloudflareAccessAuthorizationError(Exception):
    """The authenticated Cloudflare Access user is not allowed."""


class CloudflareAccessVerifier:
    """Fetch and cache Cloudflare Access signing keys without logging assertions."""

    def __init__(self, team_domain: str, audience: str, allowed_email: str | None = None) -> None:
        self._team_domain = team_domain
        self._audience = audience
        self._allowed_email = allowed_email
        self._jwks_url = f"{team_domain}/cdn-cgi/access/certs"
        self._keys: dict[str, PyJWK] = {}
        self._refresh_lock = asyncio.Lock()

    async def verify(self, assertion: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(assertion)
            if header.get("alg") != "RS256":
                raise CloudflareAccessAuthenticationError("unexpected signing algorithm")
            kid = header.get("kid")
            if not isinstance(kid, str) or not kid:
                raise CloudflareAccessAuthenticationError("missing signing key id")

            key = self._keys.get(kid)
            if key is None:
                await self._refresh_keys()
                key = self._keys.get(kid)
            if key is None:
                raise CloudflareAccessAuthenticationError("unknown signing key id")

            claims = jwt.decode(
                assertion,
                key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._team_domain,
                options={"require": ["exp", "iss", "aud"]},
            )
        except CloudflareAccessAuthenticationError:
            raise
        except (InvalidTokenError, ValueError, TypeError) as exc:
            logger.warning("Cloudflare Access JWT rejected: %s", type(exc).__name__)
            raise CloudflareAccessAuthenticationError("invalid assertion") from exc

        if self._allowed_email is not None and claims.get("email") != self._allowed_email:
            logger.warning("Cloudflare Access JWT rejected: email_not_allowed")
            raise CloudflareAccessAuthorizationError("email is not allowed")
        return claims

    async def _refresh_keys(self) -> None:
        async with self._refresh_lock:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(self._jwks_url)
                    response.raise_for_status()
                    payload = response.json()
                keys = payload.get("keys")
                if not isinstance(keys, list):
                    raise ValueError("JWKS keys is not a list")
                parsed_keys = {
                    key_data["kid"]: PyJWK.from_dict(key_data)
                    for key_data in keys
                    if isinstance(key_data, dict) and isinstance(key_data.get("kid"), str)
                }
                if not parsed_keys:
                    raise ValueError("JWKS has no usable keys")
                self._keys = parsed_keys
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                logger.warning("Cloudflare Access JWKS refresh failed: %s", type(exc).__name__)
                raise CloudflareAccessAuthenticationError("could not verify assertion") from exc


class CloudflareAccessMiddleware:
    """Require a valid Cloudflare Access assertion before reaching ``/mcp``."""

    def __init__(self, app: ASGIApp, verifier: CloudflareAccessVerifier) -> None:
        self.app = app
        self.verifier = verifier

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] != "/mcp":
            await self.app(scope, receive, send)
            return

        assertion = Headers(scope=scope).get("cf-access-jwt-assertion")
        if not assertion:
            await self._send_error(send, 401, "authentication_required")
            return

        try:
            await self.verifier.verify(assertion)
        except CloudflareAccessAuthorizationError:
            await self._send_error(send, 403, "access_forbidden")
            return
        except CloudflareAccessAuthenticationError:
            await self._send_error(send, 401, "invalid_assertion")
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _send_error(send: Send, status_code: int, error: str) -> None:
        body = f'{{"error":"{error}"}}'.encode()
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
