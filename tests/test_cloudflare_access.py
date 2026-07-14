import asyncio
import json
from datetime import UTC, datetime, timedelta

import jwt
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import Response
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from trilium_mcp.cloudflare_access import (
    CloudflareAccessAuthenticationError,
    CloudflareAccessMiddleware,
    CloudflareAccessVerifier,
)

TEAM_DOMAIN = "https://example.cloudflareaccess.com"
AUDIENCE = "cloudflare-access-audience"


def _key_pair(kid: str) -> tuple[object, dict[str, str]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return private_key, jwk


def _assertion(private_key: object, kid: str, **overrides: object) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": TEAM_DOMAIN,
        "aud": AUDIENCE,
        "email": "allowed@example.com",
        "exp": now + timedelta(minutes=5),
        "nbf": now - timedelta(seconds=1),
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


@respx.mock
def test_verifier_validates_cloudflare_access_jwt() -> None:
    private_key, jwk = _key_pair("key-1")
    respx.get(f"{TEAM_DOMAIN}/cdn-cgi/access/certs").respond(200, json={"keys": [jwk]})

    verifier = CloudflareAccessVerifier(TEAM_DOMAIN, AUDIENCE)
    claims = asyncio.run(verifier.verify(_assertion(private_key, "key-1")))

    assert claims["email"] == "allowed@example.com"


@respx.mock
def test_unknown_kid_refreshes_jwks_once() -> None:
    old_private_key, old_jwk = _key_pair("old-key")
    new_private_key, new_jwk = _key_pair("new-key")
    route = respx.get(f"{TEAM_DOMAIN}/cdn-cgi/access/certs")
    route.side_effect = [
        Response(200, json={"keys": [old_jwk]}),
        Response(200, json={"keys": [new_jwk]}),
    ]
    verifier = CloudflareAccessVerifier(TEAM_DOMAIN, AUDIENCE)

    asyncio.run(verifier.verify(_assertion(old_private_key, "old-key")))
    asyncio.run(verifier.verify(_assertion(new_private_key, "new-key")))

    assert route.call_count == 2


@respx.mock
def test_email_mismatch_returns_forbidden() -> None:
    private_key, jwk = _key_pair("key-1")
    respx.get(f"{TEAM_DOMAIN}/cdn-cgi/access/certs").respond(200, json={"keys": [jwk]})
    verifier = CloudflareAccessVerifier(TEAM_DOMAIN, AUDIENCE, "other@example.com")
    app = Starlette(routes=[Route("/mcp", lambda request: PlainTextResponse("reached"))])
    app.add_middleware(CloudflareAccessMiddleware, verifier=verifier)

    status_code, body = asyncio.run(
        _request(app, {"Cf-Access-Jwt-Assertion": _assertion(private_key, "key-1")})
    )

    assert status_code == 403
    assert json.loads(body) == {"error": "access_forbidden"}


def test_missing_assertion_returns_unauthorized() -> None:
    app = Starlette(routes=[Route("/mcp", lambda request: PlainTextResponse("reached"))])
    app.add_middleware(
        CloudflareAccessMiddleware,
        verifier=CloudflareAccessVerifier(TEAM_DOMAIN, AUDIENCE),
    )

    status_code, body = asyncio.run(_request(app))

    assert status_code == 401
    assert json.loads(body) == {"error": "authentication_required"}


@respx.mock
def test_expired_or_not_yet_valid_assertion_returns_unauthorized() -> None:
    private_key, jwk = _key_pair("key-1")
    respx.get(f"{TEAM_DOMAIN}/cdn-cgi/access/certs").respond(200, json={"keys": [jwk]})
    verifier = CloudflareAccessVerifier(TEAM_DOMAIN, AUDIENCE)

    async def requests() -> tuple[tuple[int, bytes], tuple[int, bytes]]:
        expired = _assertion(private_key, "key-1", exp=datetime.now(UTC) - timedelta(seconds=1))
        expired_response = await _request(
            _protected_app(verifier), {"Cf-Access-Jwt-Assertion": expired}
        )
        not_yet_valid = _assertion(
            private_key,
            "key-1",
            nbf=datetime.now(UTC) + timedelta(minutes=1),
        )
        nbf_response = await _request(
            _protected_app(verifier), {"Cf-Access-Jwt-Assertion": not_yet_valid}
        )
        return expired_response, nbf_response

    expired_response, nbf_response = asyncio.run(requests())

    assert expired_response[0] == 401
    assert nbf_response[0] == 401


@respx.mock
def test_valid_assertion_reaches_protected_endpoint() -> None:
    private_key, jwk = _key_pair("key-1")
    respx.get(f"{TEAM_DOMAIN}/cdn-cgi/access/certs").respond(200, json={"keys": [jwk]})
    app = _protected_app(CloudflareAccessVerifier(TEAM_DOMAIN, AUDIENCE))

    status_code, body = asyncio.run(
        _request(app, {"Cf-Access-Jwt-Assertion": _assertion(private_key, "key-1")})
    )

    assert status_code == 200
    assert body == b"reached"


def test_invalid_assertion_is_not_logged(caplog) -> None:
    assertion = "not-a-jwt-secret-value"
    verifier = CloudflareAccessVerifier(TEAM_DOMAIN, AUDIENCE)

    try:
        asyncio.run(verifier.verify(assertion))
    except CloudflareAccessAuthenticationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("invalid assertion was accepted")

    assert assertion not in caplog.text


def _protected_app(verifier: CloudflareAccessVerifier) -> Starlette:
    app = Starlette(routes=[Route("/mcp", lambda request: PlainTextResponse("reached"))])
    app.add_middleware(CloudflareAccessMiddleware, verifier=verifier)
    return app


async def _request(app: Starlette, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    messages: list[dict[str, object]] = []
    request_sent = False

    async def receive() -> dict[str, object]:
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/mcp",
            "raw_path": b"/mcp",
            "query_string": b"",
            "headers": [
                (name.lower().encode(), value.encode()) for name, value in (headers or {}).items()
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 443),
        },
        receive,
        send,
    )
    status_code = next(
        message["status"] for message in messages if message["type"] == "http.response.start"
    )
    body = b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )
    return status_code, body
