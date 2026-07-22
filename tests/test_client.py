import json
import logging

import httpx
import pytest
import respx

from trilium_mcp.client import TriliumClient
from trilium_mcp.config import Settings
from trilium_mcp.errors import (
    TriliumAPIError,
    TriliumAuthenticationError,
    TriliumConnectionError,
    TriliumNotFoundError,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings() -> Settings:
    return Settings(etapi_url="https://notes.example.com/etapi", etapi_token="secret-token")


@pytest.mark.anyio
@respx.mock
async def test_search_sends_token_and_returns_results(settings: Settings) -> None:
    route = respx.get("https://notes.example.com/etapi/notes").mock(
        return_value=httpx.Response(200, json={"results": [{"noteId": "abc"}]})
    )
    client = TriliumClient(settings)

    assert await client.search_notes(query="project", limit=3) == [{"noteId": "abc"}]
    assert route.called
    request = route.calls.last.request
    assert request.headers["Authorization"] == "secret-token"
    assert request.url.params["search"] == "project"
    await client.aclose()


@pytest.mark.anyio
@respx.mock
async def test_replace_note_content_sends_plain_text(settings: Settings) -> None:
    route = respx.put("https://notes.example.com/etapi/notes/n1/content").mock(
        return_value=httpx.Response(204)
    )
    client = TriliumClient(settings)

    await client.replace_note_content("n1", "updated body")

    request = route.calls.last.request
    assert request.headers["Content-Type"] == "text/plain; charset=utf-8"
    assert request.content == b"updated body"
    await client.aclose()


@pytest.mark.anyio
@respx.mock
async def test_get_day_note_uses_calendar_endpoint(settings: Settings) -> None:
    route = respx.get("https://notes.example.com/etapi/calendar/days/2026-07-22").mock(
        return_value=httpx.Response(200, json={"noteId": "day", "title": "22 - Wednesday"})
    )
    client = TriliumClient(settings)

    assert (await client.get_day_note("2026-07-22"))["noteId"] == "day"
    assert route.called
    await client.aclose()


@pytest.mark.anyio
@respx.mock
async def test_get_week_note_uses_calendar_endpoint(settings: Settings) -> None:
    route = respx.get("https://notes.example.com/etapi/calendar/weeks/2026-W30").mock(
        return_value=httpx.Response(200, json={"noteId": "week", "title": "Week 30"})
    )
    client = TriliumClient(settings)

    assert (await client.get_week_note("2026-W30"))["noteId"] == "week"
    assert route.called
    await client.aclose()


@pytest.mark.anyio
@respx.mock
async def test_create_note_sends_text_note_payload(settings: Settings) -> None:
    route = respx.post("https://notes.example.com/etapi/create-note").mock(
        return_value=httpx.Response(201, json={"note": {}, "branch": {}})
    )
    client = TriliumClient(settings)

    await client.create_note(parent_note_id="parent", title="New note", content="body")

    assert json.loads(route.calls.last.request.content) == {
        "parentNoteId": "parent",
        "title": "New note",
        "type": "text",
        "content": "body",
    }
    await client.aclose()


@pytest.mark.anyio
@respx.mock
async def test_rename_revision_and_branch_requests(settings: Settings) -> None:
    rename = respx.patch("https://notes.example.com/etapi/notes/n1").mock(
        return_value=httpx.Response(200, json={"noteId": "n1", "title": "Renamed"})
    )
    revision = respx.post("https://notes.example.com/etapi/notes/n1/revision").mock(
        return_value=httpx.Response(204)
    )
    branch = respx.post("https://notes.example.com/etapi/branches").mock(
        return_value=httpx.Response(201, json={"branchId": "new"})
    )
    delete = respx.delete("https://notes.example.com/etapi/branches/old").mock(
        return_value=httpx.Response(204)
    )
    client = TriliumClient(settings)

    assert (await client.rename_note("n1", "Renamed"))["title"] == "Renamed"
    await client.create_revision("n1")
    created, is_new = await client.create_branch(
        note_id="n1", parent_note_id="parent", prefix="", note_position=10, is_expanded=False
    )
    await client.delete_branch("old")

    assert json.loads(rename.calls.last.request.content) == {"title": "Renamed"}
    assert created == {"branchId": "new"}
    assert is_new is True
    assert revision.called and branch.called and delete.called
    await client.aclose()


@pytest.mark.anyio
@respx.mock
async def test_get_note_not_found(settings: Settings) -> None:
    respx.get("https://notes.example.com/etapi/notes/missing").mock(
        return_value=httpx.Response(404)
    )
    client = TriliumClient(settings)

    with pytest.raises(TriliumNotFoundError, match="not found"):
        await client.get_note("missing")
    await client.aclose()


@pytest.mark.anyio
@respx.mock
async def test_authentication_failure_is_safe(settings: Settings) -> None:
    respx.get("https://notes.example.com/etapi/notes/n1").mock(return_value=httpx.Response(401))
    client = TriliumClient(settings)

    with pytest.raises(TriliumAuthenticationError) as error:
        await client.get_note("n1")
    assert "secret-token" not in str(error.value)
    await client.aclose()


@pytest.mark.anyio
@respx.mock
async def test_server_error_and_invalid_json_are_safe(settings: Settings) -> None:
    route = respx.get("https://notes.example.com/etapi/notes/n1")
    route.mock(return_value=httpx.Response(500, text="server internals"))
    client = TriliumClient(settings)

    with pytest.raises(TriliumAPIError, match="service returned an error"):
        await client.get_note("n1")

    route.mock(return_value=httpx.Response(200, content=b"not-json"))
    with pytest.raises(TriliumAPIError, match="invalid JSON"):
        await client.get_note("n1")
    await client.aclose()


@pytest.mark.anyio
@respx.mock
async def test_timeout_and_logs_do_not_leak_token(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    respx.get("https://notes.example.com/etapi/notes/n1").mock(side_effect=httpx.ReadTimeout("timeout"))
    client = TriliumClient(settings)

    with caplog.at_level(logging.INFO):
        with pytest.raises(TriliumConnectionError, match="timed out"):
            await client.get_note("n1")
    assert "secret-token" not in caplog.text
    await client.aclose()
