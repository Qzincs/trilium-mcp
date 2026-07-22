from typing import Any

import pytest

from trilium_mcp.config import Settings
from trilium_mcp.errors import (
    TriliumAPIError,
    TriliumConflictError,
    TriliumNotFoundError,
    TriliumWriteConfirmationRequired,
)
from trilium_mcp.server import create_server
from trilium_mcp.tools.branches import get_note_locations_tool, list_note_children_tool
from trilium_mcp.tools.notes import (
    append_note_tool,
    create_note_tool,
    edit_note_content_tool,
    find_in_note_tool,
    get_day_note_tool,
    get_note_content_tool,
    get_note_tool,
    get_recent_notes_tool,
    get_week_note_tool,
    move_note_tool,
    rename_note_tool,
    replace_note_content_tool,
    search_notes_tool,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeClient:
    def __init__(self) -> None:
        self.content_calls = 0
        self.search_calls: list[dict[str, Any]] = []
        self.replacements: list[tuple[str, str]] = []
        self.creations: list[dict[str, str]] = []
        self.revisions: list[str] = []

    async def search_notes(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.search_calls.append(kwargs)
        return [
            {
                "noteId": "n1",
                "title": "First",
                "type": "text",
                "mime": "text/html",
                "dateCreated": "2026-01-01",
                "dateModified": "2026-01-02",
                "isArchived": False,
                "blobId": "b1",
                "attributes": [{"private": "value"}],
            }
        ]

    async def get_note(self, note_id: str) -> dict[str, Any]:
        return {
            "noteId": note_id,
            "title": "First",
            "type": "text",
            "blobId": "b1",
            "childBranchIds": [],
        }

    async def get_note_content(self, note_id: str) -> str:
        self.content_calls += 1
        return "abcdefghij"

    async def replace_note_content(self, note_id: str, content: str) -> None:
        self.replacements.append((note_id, content))

    async def create_revision(self, note_id: str) -> None:
        self.revisions.append(note_id)

    async def rename_note(self, note_id: str, title: str) -> dict[str, Any]:
        return {"noteId": note_id, "title": title, "type": "text", "blobId": "b1"}

    async def create_note(self, **kwargs: str) -> dict[str, Any]:
        self.creations.append(kwargs)
        return {
            "note": {"noteId": "new", "title": kwargs["title"], "type": "text", "blobId": "b2"},
            "branch": {
                "branchId": "branch",
                "noteId": "new",
                "parentNoteId": kwargs["parent_note_id"],
            },
        }

    async def list_note_children(self, note_id: str) -> list[dict[str, Any]]:
        return [
            {
                "branch": {
                    "branchId": "b2",
                    "noteId": "n2",
                    "prefix": "",
                    "notePosition": 20,
                    "isExpanded": False,
                },
                "note": {"title": "Second"},
            },
            {
                "branch": {
                    "branchId": "b1",
                    "noteId": "n1",
                    "prefix": "P",
                    "notePosition": 10,
                    "isExpanded": True,
                },
                "note": {"title": "First"},
            },
        ]


@pytest.mark.anyio
async def test_search_trims_results_and_rejects_invalid_limit() -> None:
    client = FakeClient()

    result = await search_notes_tool(client, query="first", limit=1, maximum=50)
    assert result == {
        "results": [
            {
                "noteId": "n1",
                "title": "First",
                "type": "text",
                "mime": "text/html",
                "dateCreated": "2026-01-01",
                "dateModified": "2026-01-02",
                "isArchived": False,
                "blobId": "b1",
            }
        ]
    }
    with pytest.raises(ValueError, match="between 1 and 50"):
        await search_notes_tool(client, query="first", limit=51, maximum=50)


@pytest.mark.anyio
async def test_get_note_returns_metadata_without_requesting_content() -> None:
    client = FakeClient()

    result = await get_note_tool(client, note_id="n1")
    assert result["note"]["noteId"] == "n1"
    assert result["note"]["parentBranchIds"] == []
    assert result["note"]["childBranchIds"] == []
    assert client.content_calls == 0

    content = await get_note_content_tool(client, note_id="n1", maximum=5)
    assert content["content"] == "abcde"
    assert content["blob_id"] == "b1"
    assert content["truncated"] is True


@pytest.mark.anyio
async def test_get_day_note_returns_existing_note_or_requires_creation_confirmation() -> None:
    client = FakeClient()

    async def existing_day_note(**kwargs: Any) -> list[dict[str, str]]:
        client.search_calls.append(kwargs)
        return [{"noteId": "day-note"}]

    client.search_notes = existing_day_note  # type: ignore[method-assign]

    existing = await get_day_note_tool(client, date="2026-07-22")

    assert existing == {
        "date": "2026-07-22",
        "created": False,
        "note": {
            "noteId": "day-note",
            "title": "First",
            "type": "text",
            "mime": None,
            "dateCreated": None,
            "dateModified": None,
            "isArchived": None,
            "blobId": "b1",
        },
    }
    assert client.search_calls[-1] == {
        "query": "#dateNote = '2026-07-22'",
        "limit": 2,
    }

    client.search_notes = lambda **kwargs: async_value([])  # type: ignore[method-assign]
    with pytest.raises(TriliumWriteConfirmationRequired, match="confirm_create"):
        await get_day_note_tool(client, date="2026-07-23")

    created_dates: list[str] = []

    async def create_day_note(date: str) -> dict[str, Any]:
        created_dates.append(date)
        return {"noteId": "new-day", "title": "23 - Thursday", "type": "text", "blobId": "b2"}

    client.get_day_note = create_day_note  # type: ignore[method-assign]
    created = await get_day_note_tool(client, date="2026-07-23", confirm_create=True)

    assert created["created"] is True
    assert created["note"]["noteId"] == "new-day"
    assert created_dates == ["2026-07-23"]
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        await get_day_note_tool(client, date="2026/07/23")


@pytest.mark.anyio
async def test_get_week_note_returns_existing_note_or_requires_creation_confirmation() -> None:
    client = FakeClient()

    async def existing_week_note(**kwargs: Any) -> list[dict[str, str]]:
        client.search_calls.append(kwargs)
        return [{"noteId": "week-note"}]

    client.search_notes = existing_week_note  # type: ignore[method-assign]
    existing = await get_week_note_tool(client, week="2026-W30")

    assert existing["week"] == "2026-W30"
    assert existing["created"] is False
    assert existing["note"]["noteId"] == "week-note"
    assert client.search_calls[-1] == {"query": "#weekNote = '2026-W30'", "limit": 2}

    client.search_notes = lambda **kwargs: async_value([])  # type: ignore[method-assign]
    with pytest.raises(TriliumWriteConfirmationRequired, match="confirm_create"):
        await get_week_note_tool(client, week="2026-W31")

    async def create_week_note(week: str) -> dict[str, Any]:
        return {"noteId": "new-week", "title": "Week 31", "type": "text", "blobId": "b2"}

    client.get_week_note = create_week_note  # type: ignore[method-assign]
    created = await get_week_note_tool(client, week="2026-W31", confirm_create=True)

    assert created["created"] is True
    assert created["note"]["noteId"] == "new-week"
    with pytest.raises(ValueError, match="YYYY-Www"):
        await get_week_note_tool(client, week="2026-W54")


@pytest.mark.anyio
async def test_content_uses_character_offsets_and_safe_end_offsets() -> None:
    client = FakeClient()
    client.get_note_content = lambda note_id: async_value("甲乙丙丁")  # type: ignore[method-assign]

    chunk = await get_note_content_tool(client, note_id="n1", maximum=5, offset=1, limit=2)
    assert chunk == {
        "note_id": "n1",
        "mime": None,
        "blob_id": "b1",
        "content": "乙丙",
        "offset": 1,
        "returned_length": 2,
        "total_length": 4,
        "next_offset": 3,
        "has_more": True,
        "truncated": True,
    }
    beyond_end = await get_note_content_tool(client, note_id="n1", maximum=5, offset=10, limit=2)
    assert beyond_end["content"] == ""
    assert beyond_end["has_more"] is False
    at_end = await get_note_content_tool(client, note_id="n1", maximum=5, offset=4, limit=2)
    assert at_end["content"] == ""
    assert at_end["next_offset"] is None
    with pytest.raises(ValueError, match="greater than or equal"):
        await get_note_content_tool(client, note_id="n1", maximum=5, offset=-1)
    with pytest.raises(ValueError, match="between 1 and 5"):
        await get_note_content_tool(client, note_id="n1", maximum=5, limit=6)


async def async_value(value: str) -> str:
    return value


@pytest.mark.anyio
async def test_find_in_note_returns_total_and_limited_context() -> None:
    client = FakeClient()
    client.get_note_content = lambda note_id: async_value("One one ONE")  # type: ignore[method-assign]

    found = await find_in_note_tool(
        client,
        note_id="n1",
        text="one",
        context_chars=1,
        max_matches=2,
        case_sensitive=False,
    )
    assert found["blob_id"] == "b1"
    assert found["total_matches"] == 3
    assert found["results_truncated"] is True
    assert found["returned_matches"] == 2
    assert found["matches"] == [
        {
            "start": 0,
            "end": 3,
            "context_start": 0,
            "context_end": 4,
            "context": "One ",
            "match": "One",
        },
        {
            "start": 4,
            "end": 7,
            "context_start": 3,
            "context_end": 8,
            "context": " one ",
            "match": "one",
        },
    ]
    with pytest.raises(ValueError, match="must not be empty"):
        await find_in_note_tool(client, note_id="n1", text="")
    not_found = await find_in_note_tool(client, note_id="n1", text="absent")
    assert not_found["total_matches"] == 0
    assert not_found["returned_matches"] == 0
    assert not_found["results_truncated"] is False
    assert not_found["matches"] == []

    client.get_note_content = lambda note_id: async_value("甲乙甲")  # type: ignore[method-assign]
    chinese = await find_in_note_tool(client, note_id="n1", text="甲", max_matches=10)
    assert [(match["start"], match["end"]) for match in chinese["matches"]] == [(0, 1), (2, 3)]


@pytest.mark.anyio
async def test_recent_and_children_are_ordered() -> None:
    client = FakeClient()

    recent = await get_recent_notes_tool(client, limit=1, maximum=50, include_archived=True)
    assert recent["results"][0]["noteId"] == "n1"
    assert client.search_calls[0]["query"] == "note.dateModified >= '1970-01-01'"
    assert client.search_calls[0]["order_by"] == "dateModified"
    assert client.search_calls[0]["order_direction"] == "desc"
    assert client.search_calls[0]["include_archived"] is True

    children = await list_note_children_tool(client, note_id="root")
    assert [child["branchId"] for child in children["results"]] == ["b1", "b2"]


@pytest.mark.anyio
async def test_writes_require_current_blob_and_append_content() -> None:
    client = FakeClient()

    await replace_note_content_tool(
        client, note_id="n1", content="replacement", expected_blob_id="b1", confirm=True
    )
    await append_note_tool(
        client, note_id="n1", content=" appended", expected_blob_id="b1", confirm=True
    )
    assert client.replacements == [("n1", "replacement"), ("n1", "abcdefghij appended")]

    with pytest.raises(TriliumConflictError, match="changed since it was read"):
        await replace_note_content_tool(
            client,
            note_id="n1",
            content="replacement",
            expected_blob_id="stale",
            confirm=True,
        )

    with pytest.raises(TriliumWriteConfirmationRequired, match="requires confirmation"):
        await append_note_tool(client, note_id="n1", content="ignored", expected_blob_id="b1")


@pytest.mark.anyio
async def test_create_note_requires_confirmation() -> None:
    client = FakeClient()

    with pytest.raises(TriliumWriteConfirmationRequired, match="requires confirmation"):
        await create_note_tool(client, parent_note_id="parent", title="New", content="body")

    created = await create_note_tool(
        client, parent_note_id="parent", title="New", content="body", confirm=True
    )
    assert client.creations == [{"parent_note_id": "parent", "title": "New", "content": "body"}]
    assert created["note"]["noteId"] == "new"


@pytest.mark.anyio
async def test_edit_and_rename_validate_blob_before_writing() -> None:
    client = FakeClient()

    edited = await edit_note_content_tool(
        client,
        note_id="n1",
        old_text="cde",
        new_text="XYZ",
        expected_occurrences=1,
        expected_blob_id="b1",
        confirm=True,
    )
    assert edited["replacements"] == 1
    assert client.revisions == ["n1"]
    assert client.replacements[-1] == ("n1", "abXYZfghij")

    with pytest.raises(TriliumConflictError, match="occurred 0 times"):
        await edit_note_content_tool(
            client,
            note_id="n1",
            old_text="missing",
            new_text="x",
            expected_occurrences=1,
            expected_blob_id="b1",
            confirm=True,
        )
    assert client.revisions == ["n1"]

    with pytest.raises(ValueError, match="must not be empty"):
        await edit_note_content_tool(
            client,
            note_id="n1",
            old_text="",
            new_text="x",
            expected_occurrences=1,
            expected_blob_id="b1",
            confirm=True,
        )

    renamed = await rename_note_tool(
        client, note_id="n1", new_title="Renamed", expected_blob_id="b1", confirm=True
    )
    assert renamed["note"]["title"] == "Renamed"


class FakeMoveClient:
    def __init__(self, *, delete_fails: bool = False) -> None:
        self.delete_fails = delete_fails
        self.calls: list[str] = []

    async def get_branch(self, branch_id: str) -> dict[str, Any]:
        self.calls.append(f"get_branch:{branch_id}")
        if branch_id == "old":
            return {
                "branchId": "old",
                "noteId": "note",
                "parentNoteId": "old-parent",
                "prefix": None,
                "notePosition": 10,
                "isExpanded": True,
            }
        raise AssertionError(f"unexpected branch {branch_id}")

    async def get_note(self, note_id: str) -> dict[str, Any]:
        self.calls.append(f"get_note:{note_id}")
        if note_id == "new-parent":
            return {"noteId": note_id, "parentNoteIds": [], "childBranchIds": []}
        if note_id == "note":
            return {"noteId": note_id, "parentNoteIds": [], "childBranchIds": []}
        raise AssertionError(f"unexpected note {note_id}")

    async def create_branch(self, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        self.calls.append("create_branch")
        assert kwargs["note_id"] == "note"
        assert kwargs["parent_note_id"] == "new-parent"
        return {"branchId": "new"}, True

    async def delete_branch(self, branch_id: str) -> None:
        self.calls.append(f"delete_branch:{branch_id}")
        if self.delete_fails:
            raise TriliumAPIError("Trilium rejected the request.")


@pytest.mark.anyio
async def test_move_creates_before_deleting_and_reports_partial_success() -> None:
    client = FakeMoveClient()

    moved = await move_note_tool(
        client, branch_id="old", new_parent_note_id="new-parent", confirm=True
    )
    assert moved == {
        "note_id": "note",
        "old_branch_id": "old",
        "new_branch_id": "new",
        "old_parent_note_id": "old-parent",
        "new_parent_note_id": "new-parent",
        "old_branch_deleted": True,
        "partial_success": False,
    }
    assert client.calls.index("create_branch") < client.calls.index("delete_branch:old")

    partial_client = FakeMoveClient(delete_fails=True)
    partial = await move_note_tool(
        partial_client, branch_id="old", new_parent_note_id="new-parent", confirm=True
    )
    assert partial["partial_success"] is True
    assert partial["old_branch_deleted"] is False
    assert partial["new_branch_id"] == "new"


@pytest.mark.anyio
async def test_move_rejects_unconfirmed_or_self_target() -> None:
    client = FakeMoveClient()

    with pytest.raises(TriliumWriteConfirmationRequired):
        await move_note_tool(client, branch_id="old", new_parent_note_id="new-parent")
    with pytest.raises(TriliumConflictError, match="below itself"):
        await move_note_tool(client, branch_id="old", new_parent_note_id="note", confirm=True)


class FakeLocationsClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.notes = {
            "note": {"noteId": "note", "title": "Leaf", "parentBranchIds": ["b2", "b1"]},
            "parent-a": {"noteId": "parent-a", "title": "Same", "parentBranchIds": ["pa"]},
            "parent-b": {"noteId": "parent-b", "title": "Same", "parentBranchIds": ["pb"]},
            "root": {"noteId": "root", "title": "Root", "parentBranchIds": []},
            "orphan": {"noteId": "orphan", "title": "Orphan", "parentBranchIds": []},
        }
        self.branches = {
            "b1": {
                "branchId": "b1",
                "noteId": "note",
                "parentNoteId": "parent-a",
                "prefix": None,
                "notePosition": 20,
                "isExpanded": True,
            },
            "b2": {
                "branchId": "b2",
                "noteId": "note",
                "parentNoteId": "parent-b",
                "prefix": "P",
                "notePosition": 10,
                "isExpanded": False,
            },
            "pa": {"branchId": "pa", "parentNoteId": "root", "notePosition": 1},
            "pb": {"branchId": "pb", "parentNoteId": "root", "notePosition": 1},
        }

    async def get_note(self, note_id: str) -> dict[str, Any]:
        self.calls.append(f"note:{note_id}")
        if note_id not in self.notes:
            raise TriliumNotFoundError("The requested Trilium note or resource was not found.")
        return self.notes[note_id]

    async def get_branch(self, branch_id: str) -> dict[str, Any]:
        self.calls.append(f"branch:{branch_id}")
        return self.branches[branch_id]


@pytest.mark.anyio
async def test_note_locations_are_complete_stable_and_can_skip_paths() -> None:
    client = FakeLocationsClient()

    locations = await get_note_locations_tool(client, note_id="note", include_paths=True)
    assert locations["location_count"] == 2
    assert [location["branch_id"] for location in locations["locations"]] == ["b1", "b2"]
    assert locations["locations"][0]["path"] == "Same/Leaf"
    assert locations["locations"][1]["prefix"] == "P"

    no_paths_client = FakeLocationsClient()
    no_paths = await get_note_locations_tool(no_paths_client, note_id="note", include_paths=False)
    assert "path" not in no_paths["locations"][0]
    assert "branch:pa" not in no_paths_client.calls
    assert "branch:pb" not in no_paths_client.calls


@pytest.mark.anyio
async def test_note_locations_handles_empty_and_missing_notes() -> None:
    client = FakeLocationsClient()
    empty = await get_note_locations_tool(client, note_id="orphan")
    assert empty["location_count"] == 0
    assert empty["locations"] == []

    with pytest.raises(TriliumNotFoundError):
        await get_note_locations_tool(client, note_id="missing")


@pytest.mark.anyio
async def test_note_locations_marks_path_cycles() -> None:
    client = FakeLocationsClient()
    client.notes["cycle-note"] = {
        "noteId": "cycle-note",
        "title": "Leaf",
        "parentBranchIds": ["cycle-branch"],
    }
    client.notes["cycle-parent"] = {
        "noteId": "cycle-parent",
        "title": "Parent",
        "parentBranchIds": ["cycle-parent-branch"],
    }
    client.branches["cycle-branch"] = {
        "branchId": "cycle-branch",
        "noteId": "cycle-note",
        "parentNoteId": "cycle-parent",
        "notePosition": 1,
    }
    client.branches["cycle-parent-branch"] = {
        "branchId": "cycle-parent-branch",
        "noteId": "cycle-parent",
        "parentNoteId": "cycle-note",
        "notePosition": 1,
    }

    location = (await get_note_locations_tool(client, note_id="cycle-note"))["locations"][0]
    assert location["path_status"] == "cycle"
    assert location["path"].endswith("[cycle]")


@pytest.mark.anyio
async def test_tool_annotations_distinguish_reads_and_writes() -> None:
    settings = Settings(etapi_url="https://notes.example.com/etapi", etapi_token="test-token")
    tools = await create_server(settings).list_tools()

    assert {tool.name for tool in tools} == {
        "search_notes",
        "get_note",
        "get_day_note",
        "get_week_note",
        "get_note_content",
        "list_note_children",
        "get_recent_notes",
        "replace_note_content",
        "append_note",
        "create_note",
        "edit_note_content",
        "rename_note",
        "move_note",
        "get_note_locations",
        "find_in_note",
    }
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.openWorldHint is False
        if tool.name in {
            "replace_note_content",
            "edit_note_content",
            "rename_note",
            "move_note",
        }:
            assert tool.annotations.readOnlyHint is False
            assert tool.annotations.destructiveHint is True
        elif tool.name == "append_note":
            assert tool.annotations.readOnlyHint is False
            assert tool.annotations.destructiveHint is False
        elif tool.name in {"create_note", "get_day_note", "get_week_note"}:
            assert tool.annotations.readOnlyHint is False
            assert tool.annotations.destructiveHint is False
        else:
            assert tool.annotations.readOnlyHint is True
            assert tool.annotations.destructiveHint is False
