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

import asyncio
import json
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_QUERY_PATH = "/zhiexa/library/api/v1/file/public/query"


_ID_KEYS = ("doc_id", "file_id", "fileId", "docId", "id")


def _extract_id(x) -> str:
    """Pull a plain id string out of an element that may be a str or an object.

    doc_ids sometimes stores objects ([{"doc_id": "...", "name": "..."}]) rather
    than plain id strings; the public query API wants the id string only.
    """
    if isinstance(x, dict):
        for k in _ID_KEYS:
            v = x.get(k)
            if v:
                return str(v).strip()
        return ""
    return str(x).strip()


def _parse_doc_ids(doc_ids: str | None) -> list[str]:
    """doc_ids is stored as a JSON id list ('["id1","id2"]' or '[{"doc_id":...}]');
    be tolerant of a bare/comma-separated id too. '', '[]', 'null' mean "no files"."""
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
        return [i for i in (_extract_id(x) for x in parsed) if i]
    if isinstance(parsed, dict):
        one = _extract_id(parsed)
        return [one] if one else []
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


# public/query is ~linear in id count (~100ms/id), so keep batches small; a stalled
# big batch would otherwise drag the whole list request. Cache text_length in Redis
# (immutable per file) so repeated list/paging requests avoid re-querying.
_TEXTLEN_CHUNK = 30
_TEXTLEN_TTL = 15 * 24 * 3600            # 15 天
_TEXTLEN_CACHE_PREFIX = "libtextlen:"

_redis = None


def _get_redis():
    global _redis
    if _redis is None and settings.redis_url:
        import redis.asyncio as aioredis  # lazy
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def resolve_text_lengths(file_ids: list[str]) -> dict[str, int]:
    """Resolve {file_id: text_length} for many files, Redis-cached (15d TTL).

    Feeds the 附件大小 list filter, which sums each case's 文件字数. text_length is
    immutable per file, so results are cached in Redis and only cache-miss ids hit
    the (linear-cost) library API, in small batches. Each completed batch is written
    to cache immediately, so even a partial/timed-out run warms the cache for next
    time. Best-effort: on any failure the map is simply partial.
    """
    ids = list(dict.fromkeys(i for i in (str(x).strip() for x in (file_ids or [])) if i))
    if not ids or not settings.library_service:
        return {}

    out: dict[str, int] = {}
    missing = ids
    r = _get_redis()
    if r is not None:
        try:
            cached = await r.mget([_TEXTLEN_CACHE_PREFIX + i for i in ids])
            missing = []
            for i, v in zip(ids, cached):
                if v is None:
                    missing.append(i)
                    continue
                try:
                    out[i] = int(v)
                except (TypeError, ValueError):
                    missing.append(i)
        except Exception:  # noqa: BLE001 — cache is best-effort, fall back to API
            logger.exception("redis mget text_length failed")
            missing = ids

    if not missing:
        return out

    async with httpx.AsyncClient(timeout=settings.library_timeout) as client:
        async def one(chunk: list[str]) -> None:
            resolved: dict[str, int] = {}
            for f in await _query(client, {"file_ids": chunk}):
                if not isinstance(f, dict):
                    continue
                tl = f.get("text_length")
                if not isinstance(tl, (int, float)):
                    continue
                for key in _ID_KEYS:
                    v = f.get(key)
                    if v:
                        resolved[str(v).strip()] = int(tl)
            if not resolved:
                return
            out.update(resolved)
            # Persist this batch right away so a later request (or a subsequent
            # batch that times out) still benefits from what already resolved.
            if r is not None:
                try:
                    pipe = r.pipeline()
                    for k, tl in resolved.items():
                        pipe.set(_TEXTLEN_CACHE_PREFIX + k, tl, ex=_TEXTLEN_TTL)
                    await pipe.execute()
                except Exception:  # noqa: BLE001 — caching must never break the query
                    logger.exception("redis set text_length failed")

        chunks = [missing[i:i + _TEXTLEN_CHUNK] for i in range(0, len(missing), _TEXTLEN_CHUNK)]
        await asyncio.gather(*(one(c) for c in chunks))
    return out
