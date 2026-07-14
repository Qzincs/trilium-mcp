"""Read-only MCP tool for direct Trilium child branches."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from trilium_mcp.client import TriliumClient
from trilium_mcp.tools.notes import READ_ONLY

MAX_ANCESTOR_DEPTH = 100


async def list_note_children_tool(
    client: TriliumClient, *, note_id: str, limit: int = 100
) -> dict[str, list[dict[str, Any]]]:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100.")
    children = await client.list_note_children(note_id)
    results = [
        {
            "branchId": child["branch"].get("branchId"),
            "noteId": child["branch"].get("noteId"),
            "title": child["note"].get("title"),
            "prefix": child["branch"].get("prefix"),
            "notePosition": child["branch"].get("notePosition"),
            "isExpanded": child["branch"].get("isExpanded"),
        }
        for child in children
    ]
    results.sort(key=lambda item: item["notePosition"] if item["notePosition"] is not None else 0)
    return {"results": results[:limit]}


def register_branch_tools(mcp: FastMCP, client: TriliumClient) -> None:
    """Register the direct-child listing tool."""

    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "List direct child notes of a Trilium note, ordered by note position. "
            "This does not recurse into grandchildren and does not return note bodies."
        ),
    )
    async def list_note_children(note_id: str, limit: int = 100) -> dict[str, list[dict[str, Any]]]:
        return await list_note_children_tool(client, note_id=note_id, limit=limit)

    @mcp.tool(
        annotations=READ_ONLY,
        description=(
            "List every tree location for one Trilium note. Use this to obtain the branch_id "
            "required by move_note. Set include_paths=false to avoid ancestor traversal when "
            "only direct parent "
            "locations are needed."
        ),
    )
    async def get_note_locations(
        note_id: str, include_paths: bool = True
    ) -> dict[str, Any]:
        return await get_note_locations_tool(client, note_id=note_id, include_paths=include_paths)


async def get_note_locations_tool(
    client: TriliumClient, *, note_id: str, include_paths: bool = True
) -> dict[str, Any]:
    """Return every Branch location for a note, with optional stable paths."""
    notes: dict[str, dict[str, Any]] = {}
    branches: dict[str, dict[str, Any]] = {}

    async def get_note_cached(current_note_id: str) -> dict[str, Any]:
        if current_note_id not in notes:
            notes[current_note_id] = await client.get_note(current_note_id)
        return notes[current_note_id]

    async def get_branch_cached(current_branch_id: str) -> dict[str, Any]:
        if current_branch_id not in branches:
            branches[current_branch_id] = await client.get_branch(current_branch_id)
        return branches[current_branch_id]

    note = await get_note_cached(note_id)
    branch_ids = note.get("parentBranchIds", [])
    if not isinstance(branch_ids, list):
        raise ValueError("Trilium returned invalid parent branch data.")

    locations: list[dict[str, Any]] = []
    for branch_id in branch_ids:
        branch = await get_branch_cached(branch_id)
        parent_note_id = branch.get("parentNoteId")
        if not isinstance(parent_note_id, str):
            raise ValueError("Trilium returned an invalid branch response.")
        parent_note = await get_note_cached(parent_note_id)
        location: dict[str, Any] = {
            "branch_id": branch.get("branchId"),
            "note_id": note_id,
            "parent_note_id": parent_note_id,
            "parent_title": parent_note.get("title"),
            "prefix": branch.get("prefix") or "",
            "note_position": branch.get("notePosition"),
            "is_expanded": branch.get("isExpanded"),
        }
        if include_paths:
            path, path_status = await build_location_path(
                get_note_cached,
                get_branch_cached,
                note_id=note_id,
                note_title=note.get("title"),
                parent_note_id=parent_note_id,
            )
            location["path"] = path
            location["path_status"] = path_status
        locations.append(location)

    locations.sort(
        key=lambda location: (
            location.get("path", ""),
            location["parent_note_id"],
            location["note_position"] if isinstance(location["note_position"], int) else 0,
            location["branch_id"] or "",
        )
    )
    return {
        "note_id": note_id,
        "title": note.get("title"),
        "location_count": len(locations),
        "locations": locations,
    }


async def build_location_path(
    get_note_cached: Any,
    get_branch_cached: Any,
    *,
    note_id: str,
    note_title: Any,
    parent_note_id: str,
) -> tuple[str, str]:
    """Build one stable ancestor path without recursing forever on malformed data."""
    titles = [str(note_title or note_id)]
    visited_note_ids = {note_id}
    current_parent_note_id = parent_note_id
    for _ in range(MAX_ANCESTOR_DEPTH):
        if current_parent_note_id == "root":
            return "/".join(reversed(titles)), "complete"
        if current_parent_note_id in visited_note_ids:
            return "/".join(reversed(titles)) + " [cycle]", "cycle"
        visited_note_ids.add(current_parent_note_id)
        parent_note = await get_note_cached(current_parent_note_id)
        titles.append(str(parent_note.get("title") or current_parent_note_id))
        parent_branch_ids = parent_note.get("parentBranchIds", [])
        if not isinstance(parent_branch_ids, list) or not parent_branch_ids:
            return "/".join(reversed(titles)), "complete"
        parent_branches = [await get_branch_cached(branch_id) for branch_id in parent_branch_ids]
        parent_branches = [branch for branch in parent_branches if isinstance(branch, dict)]
        if not parent_branches:
            return "/".join(reversed(titles)), "complete"
        parent_branches.sort(
            key=lambda branch: (
                branch.get("parentNoteId") or "",
                branch.get("notePosition") if isinstance(branch.get("notePosition"), int) else 0,
                branch.get("branchId") or "",
            )
        )
        next_parent_note_id = parent_branches[0].get("parentNoteId")
        if not isinstance(next_parent_note_id, str):
            return "/".join(reversed(titles)) + " [invalid]", "invalid"
        current_parent_note_id = next_parent_note_id
    return "/".join(reversed(titles)) + " [max depth]", "max_depth"
