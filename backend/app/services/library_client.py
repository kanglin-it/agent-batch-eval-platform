"""File-library service client (public, no auth).

For QA modules (法研/文书/类案/搜法) the uploaded files are referenced by
doc_ids / project_id. Resolve them to OSS download links via the public query API:

    POST {library}/zhiexa/library/api/v1/file/public/query
        {"file_ids": [...], "project_id": "..."}   # at least one
    -> data.files[].oss_url

IMPORTANT: the API AND-matches file_ids + project_id when BOTH are sent
("都传时为 AND（同时匹配）"), which would drop docs that don't carry that
project_id. The ops-backend queried the two dimensions separately (doc_ids files +
project files) and merged them, so we do the same: one call per dimension, unioned.
"""
from __future__ import annotations

import json
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_QUERY_PATH = "/zhiexa/library/api/v1/file/public/query"


def _parse_doc_ids(doc_ids: str | None) -> list[str]:
    """doc_ids is stored as a JSON id list ('["id1","id2"]'); be tolerant of a
    bare/comma-separated id too. '', '[]', 'null' mean "no files"."""
    if not doc_ids:
        return []
    s = doc_ids.strip()
    if s in ("", "[]", "null"):
        return []
    try:
        parsed = json.loads(s)
    except (TypeError, ValueError):
        # not JSON — treat as comma/whitespace-separated ids
        return [p.strip() for p in s.replace(",", " ").split() if p.strip()]
    if isinstance(parsed, list):
        return [str(x).strip() for x in parsed if str(x).strip()]
    if isinstance(parsed, (str, int)):
        return [str(parsed).strip()] if str(parsed).strip() else []
    return []


async def _query(client: httpx.AsyncClient, payload: dict) -> list[dict]:
    """POST the public query API once; return data.files (or [] on any failure)."""
    try:
        r = await client.post(
            f"{settings.library_service}{_QUERY_PATH}",
            json=payload, headers={"Content-Type": "application/json"},
        )
        r.raise_for_status()
        body = r.json()
    except Exception:  # noqa: BLE001
        logger.exception("library public/query failed payload_keys=%s", list(payload))
        return []
    if body.get("code") != 200:
        logger.warning("library public/query non-200: code=%s msg=%s",
                       body.get("code"), body.get("message"))
        return []
    return (body.get("data") or {}).get("files") or []


async def resolve_oss_urls(project_id: str | None, doc_ids: str | None) -> list[dict]:
    """Resolve a QA case's files to [{url, name}] via the public library API.

    Queries file_ids and project_id as separate requests and unions the results
    (deduped by doc_id/url), so the API's AND-when-both behavior can't drop files.
    Returns [] when there is nothing to resolve or the library service is unconfigured.
    """
    file_ids = _parse_doc_ids(doc_ids)
    if not (project_id or file_ids):
        return []
    if not settings.library_service:
        logger.warning("library_service not configured; cannot resolve QA files")
        return []

    payloads: list[dict] = []
    if file_ids:
        payloads.append({"file_ids": file_ids})
    if project_id:
        payloads.append({"project_id": project_id})

    out: list[dict] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=settings.library_timeout) as client:
        for payload in payloads:
            for f in await _query(client, payload):
                if not isinstance(f, dict):
                    continue
                url = f.get("oss_url")
                if not url:
                    continue
                key = f.get("doc_id") or url
                if key in seen:
                    continue
                seen.add(key)
                out.append({"url": url, "name": f.get("file_name") or ""})

    logger.info(
        "resolve_oss_urls project_id=%s file_ids=%d -> %d file(s)",
        project_id, len(file_ids), len(out),
    )
    return out
