"""Small async client for the read-only Trilium ETAPI surface."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from trilium_mcp.config import Settings
from trilium_mcp.errors import (
    TriliumAPIError,
    TriliumAuthenticationError,
    TriliumConnectionError,
    TriliumNotFoundError,
    TriliumRateLimitError,
)

logger = logging.getLogger(__name__)


class TriliumClient:
    """ETAPI client that keeps authentication and error handling in one place."""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.etapi_url
        self._client = httpx.AsyncClient(
            timeout=settings.request_timeout,
            headers={
                "Authorization": settings.etapi_token.get_secret_value(),
                "Accept": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search_notes(
        self,
        *,
        query: str,
        limit: int,
        ancestor_note_id: str | None = None,
        order_by: str | None = None,
        order_direction: str | None = None,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        params: dict[str, str | int | bool] = {
            "search": query,
            "limit": limit,
            "includeArchivedNotes": include_archived,
        }
        if ancestor_note_id:
            params["ancestorNoteId"] = ancestor_note_id
        if order_by:
            params["orderBy"] = order_by
        if order_direction:
            params["orderDirection"] = order_direction

        payload = await self._get_json("notes", params=params)
        results = payload.get("results")
        if not isinstance(results, list):
            raise TriliumAPIError("Trilium returned an invalid search response.")
        logger.info("Trilium search completed result_count=%d", len(results))
        return [result for result in results if isinstance(result, dict)]

    async def get_note(self, note_id: str) -> dict[str, Any]:
        return await self._get_json(f"notes/{note_id}")

    async def get_note_content(self, note_id: str) -> str:
        response = await self._request("notes/" + note_id + "/content", accept="text/plain, */*")
        return response.text

    async def get_day_note(self, date: str) -> dict[str, Any]:
        return await self._get_json(f"calendar/days/{date}")

    async def get_week_note(self, week: str) -> dict[str, Any]:
        return await self._get_json(f"calendar/weeks/{week}")

    async def replace_note_content(self, note_id: str, content: str) -> None:
        await self._request(
            f"notes/{note_id}/content",
            method="PUT",
            content=content,
            content_type="text/plain; charset=utf-8",
        )

    async def create_note(self, *, parent_note_id: str, title: str, content: str) -> dict[str, Any]:
        response = await self._request(
            "create-note",
            method="POST",
            json_body={
                "parentNoteId": parent_note_id,
                "title": title,
                "type": "text",
                "content": content,
            },
        )
        try:
            payload = response.json()
        except ValueError as error:
            raise TriliumAPIError("Trilium returned an invalid JSON response.") from error
        if not isinstance(payload, dict):
            raise TriliumAPIError("Trilium returned an invalid JSON response.")
        return payload

    async def rename_note(self, note_id: str, title: str) -> dict[str, Any]:
        response = await self._request(
            f"notes/{note_id}", method="PATCH", json_body={"title": title}
        )
        return self._parse_json(response)

    async def create_revision(self, note_id: str) -> None:
        await self._request(f"notes/{note_id}/revision", method="POST")

    async def create_branch(
        self,
        *,
        note_id: str,
        parent_note_id: str,
        prefix: str,
        note_position: int,
        is_expanded: bool,
    ) -> tuple[dict[str, Any], bool]:
        response = await self._request(
            "branches",
            method="POST",
            json_body={
                "noteId": note_id,
                "parentNoteId": parent_note_id,
                "prefix": prefix,
                "notePosition": note_position,
                "isExpanded": is_expanded,
            },
        )
        return self._parse_json(response), response.status_code == 201

    async def delete_branch(self, branch_id: str) -> None:
        await self._request(f"branches/{branch_id}", method="DELETE")

    async def get_branch(self, branch_id: str) -> dict[str, Any]:
        return await self._get_json(f"branches/{branch_id}")

    async def list_note_children(self, note_id: str) -> list[dict[str, Any]]:
        parent = await self.get_note(note_id)
        branch_ids = parent.get("childBranchIds", [])
        if not isinstance(branch_ids, list):
            raise TriliumAPIError("Trilium returned invalid child branch data.")

        branches = await asyncio.gather(*(self.get_branch(branch_id) for branch_id in branch_ids))
        notes = await asyncio.gather(*(self.get_note(branch["noteId"]) for branch in branches))
        return [
            {"branch": branch, "note": note}
            for branch, note in zip(branches, notes, strict=True)
        ]

    async def _get_json(
        self, path: str, *, params: dict[str, str | int | bool] | None = None
    ) -> dict[str, Any]:
        response = await self._request(path, params=params)
        return self._parse_json(response)

    @staticmethod
    def _parse_json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise TriliumAPIError("Trilium returned an invalid JSON response.") from error
        if not isinstance(payload, dict):
            raise TriliumAPIError("Trilium returned an invalid JSON response.")
        return payload

    async def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        params: dict[str, str | int | bool] | None = None,
        accept: str | None = None,
        content: str | None = None,
        content_type: str | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        started = time.perf_counter()
        request_path = "/" + path.lstrip("/")
        headers: dict[str, str] = {}
        if accept:
            headers["Accept"] = accept
        if content_type:
            headers["Content-Type"] = content_type
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{request_path}",
                params=params,
                content=content,
                json=json_body,
                headers=headers or None,
            )
        except httpx.TimeoutException as error:
            logger.warning("Trilium request failed error_type=timeout path=%s", request_path)
            raise TriliumConnectionError("Trilium request timed out.") from error
        except httpx.RequestError as error:
            logger.warning("Trilium request failed error_type=connection path=%s", request_path)
            raise TriliumConnectionError("Could not connect to Trilium.") from error

        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "Trilium request method=%s path=%s status=%d duration_ms=%d",
            method,
            request_path,
            response.status_code,
            duration_ms,
        )
        self._raise_for_status(response.status_code)
        return response

    @staticmethod
    def _raise_for_status(status_code: int) -> None:
        if status_code in (401, 403):
            raise TriliumAuthenticationError(
                "Trilium authentication failed. Check TRILIUM_ETAPI_TOKEN."
            )
        if status_code == 404:
            raise TriliumNotFoundError("The requested Trilium note or resource was not found.")
        if status_code == 429:
            raise TriliumRateLimitError("Trilium rate limit reached. Please try again later.")
        if status_code >= 500:
            raise TriliumAPIError("Trilium service returned an error. Please try again later.")
        if status_code >= 400:
            raise TriliumAPIError("Trilium rejected the request.")
