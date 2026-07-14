"""Read-only MCP tools for Trilium notes."""

from __future__ import annotations

import re
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from trilium_mcp.client import TriliumClient
from trilium_mcp.config import Settings
from trilium_mcp.errors import (
    TriliumAPIError,
    TriliumConflictError,
    TriliumError,
    TriliumWriteConfirmationRequired,
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)
APPEND = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)
CREATE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)
NOTE_FIELDS = (
    "noteId",
    "title",
    "type",
    "mime",
    "dateCreated",
    "dateModified",
    "isArchived",
    "blobId",
)
MAX_FIND_CONTEXT_CHARS = 5_000
MAX_FIND_MATCHES = 100


def note_summary(note: dict[str, Any]) -> dict[str, Any]:
    """Keep only metadata useful for choosing a note to inspect."""
    return {field: note.get(field) for field in NOTE_FIELDS}


def validate_search_limit(limit: int, maximum: int) -> None:
    if not 1 <= limit <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}.")


async def search_notes_tool(
    client: TriliumClient,
    *,
    query: str,
    limit: int,
    maximum: int,
    ancestor_note_id: str | None = None,
    order_by: str | None = None,
    order_direction: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    validate_search_limit(limit, maximum)
    results = await client.search_notes(
        query=query,
        limit=limit,
        ancestor_note_id=ancestor_note_id,
        order_by=order_by,
        order_direction=order_direction,
    )
    return {"results": [note_summary(note) for note in results]}


async def get_note_tool(
    client: TriliumClient, *, note_id: str
) -> dict[str, Any]:
    note = await client.get_note(note_id)
    metadata = note_summary(note)
    metadata["parentBranchIds"] = note.get("parentBranchIds", [])
    metadata["childBranchIds"] = note.get("childBranchIds", [])
    return {"note": metadata}


async def get_note_content_tool(
    client: TriliumClient,
    *,
    note_id: str,
    maximum: int,
    offset: int = 0,
    limit: int | None = None,
) -> dict[str, Any]:
    if offset < 0:
        raise ValueError("offset must be greater than or equal to zero.")
    if limit is not None and not 1 <= limit <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}.")
    effective_limit = maximum if limit is None else limit
    note = await client.get_note(note_id)
    content = await client.get_note_content(note_id)
    total_length = len(content)
    returned_content = content[offset : offset + effective_limit]
    returned_length = len(returned_content)
    next_offset = offset + returned_length
    has_more = next_offset < total_length
    return {
        "note_id": note_id,
        "mime": note.get("mime"),
        "blob_id": note.get("blobId"),
        "content": returned_content,
        "offset": offset,
        "returned_length": returned_length,
        "total_length": total_length,
        "next_offset": next_offset if has_more else None,
        "has_more": has_more,
        "truncated": has_more,
    }


async def find_in_note_tool(
    client: TriliumClient,
    *,
    note_id: str,
    text: str,
    context_chars: int = 500,
    max_matches: int = 10,
    case_sensitive: bool = True,
) -> dict[str, Any]:
    if not text.strip():
        raise ValueError("text must not be empty.")
    if not 0 <= context_chars <= MAX_FIND_CONTEXT_CHARS:
        raise ValueError(f"context_chars must be between 0 and {MAX_FIND_CONTEXT_CHARS}.")
    if not 1 <= max_matches <= MAX_FIND_MATCHES:
        raise ValueError(f"max_matches must be between 1 and {MAX_FIND_MATCHES}.")
    note = await client.get_note(note_id)
    content = await client.get_note_content(note_id)
    flags = 0 if case_sensitive else re.IGNORECASE
    matches = list(re.finditer(re.escape(text), content, flags))
    results = []
    for match in matches[:max_matches]:
        start, end = match.span()
        context_start = max(0, start - context_chars)
        context_end = min(len(content), end + context_chars)
        results.append(
            {
                "start": start,
                "end": end,
                "context_start": context_start,
                "context_end": context_end,
                "context": content[context_start:context_end],
                "match": content[start:end],
            }
        )
    return {
        "note_id": note_id,
        "mime": note.get("mime"),
        "blob_id": note.get("blobId"),
        "query": text,
        "case_sensitive": case_sensitive,
        "total_matches": len(matches),
        "returned_matches": len(results),
        "matches": results,
        "results_truncated": len(matches) > max_matches,
    }


async def get_recent_notes_tool(
    client: TriliumClient, *, limit: int, maximum: int, include_archived: bool = False
) -> dict[str, list[dict[str, Any]]]:
    validate_search_limit(limit, maximum)
    results = await client.search_notes(
        query="note.dateModified >= '1970-01-01'",
        limit=limit,
        order_by="dateModified",
        order_direction="desc",
        include_archived=include_archived,
    )
    return {"results": [note_summary(note) for note in results]}


def require_current_blob(note: dict[str, Any], expected_blob_id: str) -> None:
    if note.get("blobId") != expected_blob_id:
        raise TriliumConflictError(
            "The note changed since it was read. Fetch its metadata and retry with the current "
            "blobId."
        )


def require_write_confirmation(confirm: bool) -> None:
    if not confirm:
        raise TriliumWriteConfirmationRequired(
            "Writing note content requires confirmation. Review the change and retry with "
            "confirm=true."
        )


async def replace_note_content_tool(
    client: TriliumClient,
    *,
    note_id: str,
    content: str,
    expected_blob_id: str,
    confirm: bool = False,
    create_revision: bool = True,
) -> dict[str, Any]:
    require_write_confirmation(confirm)
    note = await client.get_note(note_id)
    require_current_blob(note, expected_blob_id)
    if create_revision:
        await client.create_revision(note_id)
    await client.replace_note_content(note_id, content)
    return {"note": note_summary(await client.get_note(note_id))}


async def edit_note_content_tool(
    client: TriliumClient,
    *,
    note_id: str,
    old_text: str,
    new_text: str,
    expected_occurrences: int,
    expected_blob_id: str,
    confirm: bool = False,
    create_revision: bool = True,
) -> dict[str, Any]:
    require_write_confirmation(confirm)
    if not old_text:
        raise ValueError("old_text must not be empty.")
    if expected_occurrences < 0:
        raise ValueError("expected_occurrences must be zero or greater.")
    note = await client.get_note(note_id)
    content = await client.get_note_content(note_id)
    require_current_blob(note, expected_blob_id)
    occurrences = content.count(old_text)
    if occurrences != expected_occurrences:
        raise TriliumConflictError(
            f"old_text occurred {occurrences} times, but expected_occurrences is "
            f"{expected_occurrences}."
        )
    if create_revision:
        await client.create_revision(note_id)
    await client.replace_note_content(note_id, content.replace(old_text, new_text))
    return {"note": note_summary(await client.get_note(note_id)), "replacements": occurrences}


async def rename_note_tool(
    client: TriliumClient,
    *,
    note_id: str,
    new_title: str,
    expected_blob_id: str,
    confirm: bool = False,
) -> dict[str, Any]:
    require_write_confirmation(confirm)
    note = await client.get_note(note_id)
    require_current_blob(note, expected_blob_id)
    return {"note": note_summary(await client.rename_note(note_id, new_title))}


async def move_note_tool(
    client: TriliumClient,
    *,
    branch_id: str,
    new_parent_note_id: str,
    prefix: str | None = None,
    position: int | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    require_write_confirmation(confirm)
    old_branch = await client.get_branch(branch_id)
    note_id = old_branch.get("noteId")
    old_parent_note_id = old_branch.get("parentNoteId")
    if not isinstance(note_id, str) or not isinstance(old_parent_note_id, str):
        raise TriliumAPIError("Trilium returned an invalid branch response.")
    if branch_id == "root" or note_id == "root":
        raise TriliumConflictError("The root branch cannot be moved.")
    if new_parent_note_id == note_id:
        raise TriliumConflictError("A note cannot be moved below itself.")
    if await is_descendant_note(client, ancestor_note_id=note_id, note_id=new_parent_note_id):
        raise TriliumConflictError("A note cannot be moved below one of its descendants.")

    old_prefix = old_branch.get("prefix") or ""
    new_prefix = old_prefix if prefix is None else prefix
    new_position = old_branch.get("notePosition", 0) if position is None else position
    if not isinstance(new_prefix, str) or not isinstance(new_position, int):
        raise TriliumAPIError("Trilium returned an invalid branch response.")
    if (
        new_parent_note_id == old_parent_note_id
        and new_prefix == old_prefix
        and new_position == old_branch.get("notePosition", 0)
    ):
        raise TriliumConflictError("The move would not change the branch.")

    target_parent = await client.get_note(new_parent_note_id)
    child_branch_ids = target_parent.get("childBranchIds", [])
    if not isinstance(child_branch_ids, list):
        raise TriliumAPIError("Trilium returned invalid child branch data.")
    for target_branch_id in child_branch_ids:
        target_branch = await client.get_branch(target_branch_id)
        if target_branch.get("noteId") == note_id:
            raise TriliumConflictError("The target parent already contains this note.")

    new_branch, created = await client.create_branch(
        note_id=note_id,
        parent_note_id=new_parent_note_id,
        prefix=new_prefix,
        note_position=new_position,
        is_expanded=bool(old_branch.get("isExpanded", False)),
    )
    new_branch_id = new_branch.get("branchId")
    if not created or not isinstance(new_branch_id, str):
        raise TriliumConflictError("The target branch already exists; the old branch was kept.")
    try:
        await client.delete_branch(branch_id)
    except TriliumError as error:
        return {
            "note_id": note_id,
            "old_branch_id": branch_id,
            "new_branch_id": new_branch_id,
            "old_parent_note_id": old_parent_note_id,
            "new_parent_note_id": new_parent_note_id,
            "old_branch_deleted": False,
            "partial_success": True,
            "warning": str(error),
        }
    return {
        "note_id": note_id,
        "old_branch_id": branch_id,
        "new_branch_id": new_branch_id,
        "old_parent_note_id": old_parent_note_id,
        "new_parent_note_id": new_parent_note_id,
        "old_branch_deleted": True,
        "partial_success": False,
    }


async def is_descendant_note(
    client: TriliumClient, *, ancestor_note_id: str, note_id: str
) -> bool:
    pending = [note_id]
    visited: set[str] = set()
    while pending:
        current_note_id = pending.pop()
        if current_note_id in visited:
            continue
        visited.add(current_note_id)
        note = await client.get_note(current_note_id)
        parent_note_ids = note.get("parentNoteIds", [])
        if not isinstance(parent_note_ids, list):
            raise TriliumAPIError("Trilium returned invalid parent note data.")
        if ancestor_note_id in parent_note_ids:
            return True
        pending.extend(parent_id for parent_id in parent_note_ids if isinstance(parent_id, str))
    return False


async def append_note_tool(
    client: TriliumClient,
    *,
    note_id: str,
    content: str,
    expected_blob_id: str,
    confirm: bool = False,
) -> dict[str, Any]:
    require_write_confirmation(confirm)
    note = await client.get_note(note_id)
    require_current_blob(note, expected_blob_id)
    existing_content = await client.get_note_content(note_id)
    await client.replace_note_content(note_id, existing_content + content)
    return {"note": note_summary(await client.get_note(note_id))}


async def create_note_tool(
    client: TriliumClient,
    *,
    parent_note_id: str,
    title: str,
    content: str,
    confirm: bool = False,
) -> dict[str, Any]:
    require_write_confirmation(confirm)
    created = await client.create_note(parent_note_id=parent_note_id, title=title, content=content)
    note = created.get("note")
    branch = created.get("branch")
    if not isinstance(note, dict) or not isinstance(branch, dict):
        raise TriliumAPIError("Trilium returned an invalid create-note response.")
    return {
        "note": note_summary(note),
        "branch": {
            "branchId": branch.get("branchId"),
            "noteId": branch.get("noteId"),
            "parentNoteId": branch.get("parentNoteId"),
            "prefix": branch.get("prefix"),
            "notePosition": branch.get("notePosition"),
        },
    }


def register_note_tools(mcp: FastMCP, client: TriliumClient, settings: Settings) -> None:
    """Register the four note-oriented tools on an MCP server."""

    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "Search Trilium notes by title, content, or ETAPI search syntax. "
            "Use this to find candidate notes before reading one. Results contain metadata only, "
            "never full content."
        ),
    )
    async def search_notes(
        query: str,
        limit: int = settings.default_search_limit,
        ancestor_note_id: str | None = None,
        order_by: str | None = None,
        order_direction: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        return await search_notes_tool(
            client,
            query=query,
            limit=limit,
            maximum=settings.max_search_limit,
            ancestor_note_id=ancestor_note_id,
            order_by=order_by,
            order_direction=order_direction,
        )

    @mcp.tool(
        annotations=READ_ONLY,
        description="Get metadata for one Trilium note. Use get_note_content to read its body.",
    )
    async def get_note(note_id: str) -> dict[str, Any]:
        return await get_note_tool(client, note_id=note_id)

    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "Read raw Trilium note content. text/html notes return raw HTML, not rendered text. "
            "For long notes, use offset and limit for character-based chunks. The returned blob_id "
            "can be passed as expected_blob_id to a later editing tool."
        ),
    )
    async def get_note_content(
        note_id: str, offset: int = 0, limit: int | None = None
    ) -> dict[str, Any]:
        return await get_note_content_tool(
            client,
            note_id=note_id,
            maximum=settings.max_content_chars,
            offset=offset,
            limit=limit,
        )

    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "Find literal text in a note's raw body and return character offsets with surrounding "
            "context. For text/html notes this searches raw HTML, not rendered visible text. "
            "Use this "
            "before editing a small section of a long note."
        ),
    )
    async def find_in_note(
        note_id: str,
        text: str,
        context_chars: int = 500,
        max_matches: int = 10,
        case_sensitive: bool = True,
    ) -> dict[str, Any]:
        return await find_in_note_tool(
            client,
            note_id=note_id,
            text=text,
            context_chars=context_chars,
            max_matches=max_matches,
            case_sensitive=case_sensitive,
        )

    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "List recently modified Trilium notes without reading their bodies. "
            "Set include_archived to include archived notes."
        ),
    )
    async def get_recent_notes(
        limit: int = 20, include_archived: bool = False
    ) -> dict[str, list[dict[str, Any]]]:
        return await get_recent_notes_tool(
            client,
            limit=limit,
            maximum=settings.max_search_limit,
            include_archived=include_archived,
        )

    @mcp.tool(
        annotations=WRITE,
        description=(
            "Replace a note's entire raw text body. Call get_note first and pass its current "
            "blobId "
            "as expected_blob_id to avoid overwriting a newer version. Review the change, then set "
            "confirm=true. This modifies note content."
        ),
    )
    async def replace_note_content(
        note_id: str,
        content: str,
        expected_blob_id: str,
        confirm: bool = False,
        create_revision: bool = True,
    ) -> dict[str, Any]:
        return await replace_note_content_tool(
            client,
            note_id=note_id,
            content=content,
            expected_blob_id=expected_blob_id,
            confirm=confirm,
            create_revision=create_revision,
        )

    @mcp.tool(
        annotations=APPEND,
        description=(
            "Append raw text to a note's existing body. Call get_note first and pass its current "
            "blobId "
            "as expected_blob_id to avoid appending to a newer version. Review the change, then "
            "set "
            "confirm=true. This adds content without replacing the existing body."
        ),
    )
    async def append_note(
        note_id: str, content: str, expected_blob_id: str, confirm: bool = False
    ) -> dict[str, Any]:
        return await append_note_tool(
            client,
            note_id=note_id,
            content=content,
            expected_blob_id=expected_blob_id,
            confirm=confirm,
        )

    @mcp.tool(
        annotations=CREATE,
        description=(
            "Create a text note below a parent note. Review the parent, title, and body, then set "
            "confirm=true. This creates a new note and does not modify an existing note."
        ),
    )
    async def create_note(
        parent_note_id: str, title: str, content: str = "", confirm: bool = False
    ) -> dict[str, Any]:
        return await create_note_tool(
            client,
            parent_note_id=parent_note_id,
            title=title,
            content=content,
            confirm=confirm,
        )

    @mcp.tool(
        annotations=WRITE,
        description=(
            "Replace exact raw text in a note body. For text/html notes, old_text and new_text "
            "operate on the raw HTML string, not rendered visible text. The exact occurrence count "
            "and blobId "
            "must match before writing; set confirm=true after review."
        ),
    )
    async def edit_note_content(
        note_id: str,
        old_text: str,
        new_text: str,
        expected_blob_id: str,
        expected_occurrences: int = 1,
        confirm: bool = False,
        create_revision: bool = True,
    ) -> dict[str, Any]:
        return await edit_note_content_tool(
            client,
            note_id=note_id,
            old_text=old_text,
            new_text=new_text,
            expected_occurrences=expected_occurrences,
            expected_blob_id=expected_blob_id,
            confirm=confirm,
            create_revision=create_revision,
        )

    @mcp.tool(
        annotations=WRITE,
        description=(
            "Rename a Trilium note without changing its body. Call get_note first and pass the "
            "current "
            "blobId as expected_blob_id, then set confirm=true after review."
        ),
    )
    async def rename_note(
        note_id: str, new_title: str, expected_blob_id: str, confirm: bool = False
    ) -> dict[str, Any]:
        return await rename_note_tool(
            client,
            note_id=note_id,
            new_title=new_title,
            expected_blob_id=expected_blob_id,
            confirm=confirm,
        )

    @mcp.tool(
        annotations=WRITE,
        description=(
            "Move one specific Trilium branch to a new parent. Use branch_id, not only note_id, "
            "because notes can appear in multiple locations. It creates the new branch before "
            "deleting the old "
            "one and reports partial_success if the deletion fails."
        ),
    )
    async def move_note(
        branch_id: str,
        new_parent_note_id: str,
        prefix: str | None = None,
        position: int | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        return await move_note_tool(
            client,
            branch_id=branch_id,
            new_parent_note_id=new_parent_note_id,
            prefix=prefix,
            position=position,
            confirm=confirm,
        )
