"""File-library service client (public, no auth).

For QA modules (法研/文书/类案/搜法) the uploaded files are referenced by
doc_ids / project_id. Resolve them to OSS download links via the public query API:

    POST {library}/zhiexa/library/api/v1/file/public/query
        {"file_ids": [...], "project_id": "..."}   # at least one
    -> data.files[].oss_url
"""
from __future__ import annotations

import json
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_QUERY_PATH = "/zhiexa/library/api/v1/file/public/query"


async def resolve_oss_urls(project_id: str | None, doc_ids: str | None) -> list[dict]:
    """Resolve a QA case's files to [{url, name}] via the public library API.

    doc_ids is stored as a JSON string (e.g. '["id1","id2"]').
    Returns [] when nothing to resolve or the library service is unconfigured.
    """
    file_ids: list = []
    if doc_ids and doc_ids != "[]":
        try:
            file_ids = json.loads(doc_ids)
        except (TypeError, ValueError):
            file_ids = []
    if not (project_id or file_ids):
        return []
    if not settings.library_service:
        logger.warning("library_service not configured; cannot resolve QA files")
        return []

    payload: dict = {}
    if file_ids:
        payload["file_ids"] = file_ids
    if project_id:
        payload["project_id"] = project_id

    try:
        async with httpx.AsyncClient(timeout=settings.library_timeout) as client:
            r = await client.post(
                f"{settings.library_service}{_QUERY_PATH}",
                json=payload, headers={"Content-Type": "application/json"},
            )
        r.raise_for_status()
        body = r.json()
    except Exception:
        logger.exception("library public/query failed project_id=%s", project_id)
        return []

    if body.get("code") != 200:
        logger.warning("library public/query non-200: %s", body.get("code"))
        return []

    out: list[dict] = []
    for f in (body.get("data") or {}).get("files") or []:
        if not isinstance(f, dict):
            continue
        url = f.get("oss_url")
        if url:
            out.append({"url": url, "name": f.get("file_name") or ""})
    return out
