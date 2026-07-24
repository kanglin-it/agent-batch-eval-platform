"""File-library service client.

For QA modules (法律研究/文书起草/AI类案/AI搜法) the uploaded files are referenced
by doc_ids / project_id, not direct OSS URLs. This resolves them to OSS download
links via the library service, mirroring the ops-backend LibraryInterface:

    POST {library}/zhiexa/library/api/v1/tasks/files          {project_id, files}
    GET  {library}/zhiexa/library/api/v1/project/{id}/files
"""
from __future__ import annotations

import json
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if settings.library_token:
        h["Authorization"] = f"Bearer {settings.library_token}"
    return h


async def _get_file_list(client: httpx.AsyncClient, project_id: str, files: list) -> dict:
    payload: dict = {}
    if project_id:
        payload["project_id"] = project_id
    if files:
        payload["files"] = files
    r = await client.post(
        f"{settings.library_service}/zhiexa/library/api/v1/tasks/files",
        json=payload, headers=_headers(),
    )
    r.raise_for_status()
    body = r.json()
    if body.get("code") != 200:
        raise RuntimeError(f"library tasks/files failed: {body.get('code')}")
    return body.get("data") or {}


async def _get_project_file_list(client: httpx.AsyncClient, project_id: str) -> dict:
    r = await client.get(
        f"{settings.library_service}/zhiexa/library/api/v1/project/{project_id}/files",
        headers=_headers(),
    )
    r.raise_for_status()
    body = r.json()
    if body.get("code") != 200:
        raise RuntimeError(f"library project files failed: {body.get('code')}")
    return body.get("data") or {}


def _oss_from_items(items) -> list[dict]:
    out: list[dict] = []
    for f in items or []:
        if not isinstance(f, dict):
            continue
        url = f.get("oss_url")
        if url:
            out.append({"url": url, "name": f.get("name") or f.get("file_name") or ""})
    return out


async def resolve_oss_urls(project_id: str | None, doc_ids: str | None) -> list[dict]:
    """Resolve a QA case's files to [{url, name}] via the library service.

    doc_ids is stored as a JSON string (e.g. '["id1","id2"]').
    Returns [] when nothing to resolve or the library service is unconfigured.
    """
    files: list = []
    if doc_ids and doc_ids != "[]":
        try:
            files = json.loads(doc_ids)
        except (TypeError, ValueError):
            files = []
    if not (project_id or files):
        return []
    if not settings.library_service:
        logger.warning("library_service not configured; cannot resolve QA files")
        return []

    try:
        async with httpx.AsyncClient(timeout=settings.library_timeout) as client:
            info = await _get_file_list(client, project_id or "", files)
            if project_id:
                proj = await _get_project_file_list(client, project_id)
                info["project_list"] = proj.get("files", [])
    except Exception:
        logger.exception("resolve_oss_urls failed project_id=%s", project_id)
        return []

    return _oss_from_items(info.get("files")) + _oss_from_items(info.get("project_list"))
