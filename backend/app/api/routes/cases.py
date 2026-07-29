"""Case management (用例管理页).

Performance strategy:
  1. Query each live source in parallel (own DB session) — light LIST columns only
  2. Each source returns LIMIT (offset+page_size) light rows
  3. Merge-sort in Python, then slice the page
  4. HYDRATE score / answer / filenames for the page only
  5. Capped COUNT per source for pager totals (not a fake offset+1)
"""
from __future__ import annotations

import asyncio
import heapq
import io
import logging
import zipfile
from collections import defaultdict
from datetime import date, datetime, time, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.case_session import CaseSessionLocal, get_case_db
from app.schemas.auth import CurrentUser
from app.schemas.case import CaseFilter, CaseIdsResponse, CaseItem, CasePage
from app.services.case_data import fetch_case_data
from app.services.case_source import (
    SourceFilters,
    build_list_source_sql,
    build_page_hydrate_sql,
    parse_extra,
    resolve_sources,
)
from app.services.library_client import _parse_doc_ids, resolve_oss_urls, resolve_text_lengths
from app.services.oss_file import fetch_file_bytes, normalize_file_ref
from app.utils.text_fix import recover_chinese_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cases", tags=["cases"])

CASE_SELECTION_LIMIT = 500
MAX_PAGE = 50
MAX_PER_SOURCE = 500
MAX_PER_SOURCE_DEDUP = 800


def _zip_unique(used: set[str], fname: str) -> str:
    if fname not in used:
        used.add(fname)
        return fname
    stem, dot, ext = fname.rpartition(".")
    base = stem if dot else fname
    suffix = f".{ext}" if dot else ""
    i = 1
    while f"{base}_{i}{suffix}" in used:
        i += 1
    name = f"{base}_{i}{suffix}"
    used.add(name)
    return name


@router.get("/download-files")
async def download_case_files(
    task_id: str = Query(...),
    db: AsyncSession = Depends(get_case_db),
    _: CurrentUser = Depends(get_current_user),
):
    """Download a case's file(s). Single file → the file; multiple → a zip."""
    info = (await fetch_case_data(db, [task_id])).get(task_id)
    file_refs = (info or {}).get("files") or []
    if not file_refs:
        raise HTTPException(404, "该用例无可下载文件")

    downloaded: list[tuple[str, bytes, str]] = []
    for ref in file_refs:
        norm = normalize_file_ref(ref)
        if norm is None:
            continue
        url, name = norm
        try:
            downloaded.append(await fetch_file_bytes(url, name))
        except Exception:  # noqa: BLE001
            logger.exception("download case file failed url=%s", url[:120])
    if not downloaded:
        raise HTTPException(502, "文件下载失败")

    if len(downloaded) == 1:
        fname, blob, ctype = downloaded[0]
        return StreamingResponse(
            io.BytesIO(blob),
            media_type=ctype or "application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        used: set[str] = set()
        for fname, blob, _ctype in downloaded:
            zf.writestr(_zip_unique(used, fname), blob)
    buf.seek(0)
    zipname = quote(f"用例文件_{task_id}.zip")
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{zipname}"},
    )


def _source_filters(filters: CaseFilter) -> SourceFilters:
    extra = parse_extra(filters.extra)
    return SourceFilters(
        created_start=_parse_day(filters.created_start),
        created_end=_parse_day_end(filters.created_end),
        keyword=filters.keyword or None,
        has_file=filters.has_file,
        user_rating=filters.user_rating if filters.user_rating in ("good", "bad") else None,
        exclude_failed=bool(filters.exclude_failed),
        task_id=extra.get("task_id"),
        channel_type=extra.get("channel_type"),
        sub_function=extra.get("sub_function"),
        draft_type=extra.get("draft_type"),
    )


def _parse_day(value: str | None):
    """asyncpg needs date/datetime, not 'YYYY-MM-DD' strings."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_day_end(value: str | None):
    """Inclusive end-of-day for a date-only filter."""
    d = _parse_day(value)
    if d is None:
        return None
    return datetime.combine(d, time(23, 59, 59))


def _created_key(row: dict):
    """Normalize naive/aware timestamps so heapq.merge can compare them."""
    v = row.get("src_created")
    if v is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


async def _fetch_source_rows(
    source: str,
    *,
    schema: str,
    sf: SourceFilters,
    per_source_limit: int,
) -> list[dict]:
    sql, params = build_list_source_sql(
        schema, source, filters=sf, per_source_limit=per_source_limit,
    )
    async with CaseSessionLocal() as session:
        rows = (await session.execute(text(sql), params)).mappings().all()
    return [dict(r) for r in rows]


def _merge_desc(groups: list[list[dict]]) -> list[dict]:
    decorated = [sorted(g, key=_created_key, reverse=True) for g in groups if g]
    if not decorated:
        return []
    return list(heapq.merge(*decorated, key=_created_key, reverse=True))


# 附件大小分桶：用例所有文件字数(text_length)总和满足该比较。单选。
_ATTACHMENT_SIZE_BUCKETS = {
    "le5w": lambda n: n <= 50_000,
    "le10w": lambda n: n <= 100_000,
    "le20w": lambda n: n <= 200_000,
    "ge20w": lambda n: n >= 200_000,
}


def _row_file_ids(r: dict) -> list[str]:
    """A case row's file identifiers: QA 用 doc_ids，审查类用 file_ids(t_file_info)。"""
    raw = r.get("doc_ids") if r.get("kind") == "qa" else r.get("file_ids")
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return _parse_doc_ids(raw)  # JSON string list / bare id


async def _filter_by_attachment_size(rows: list[dict], size: str) -> list[dict]:
    """Keep rows whose total 文件字数 (Σ text_length via library) matches the bucket.

    text_length only exists in the library API, so we batch-resolve every candidate
    row's file ids in one shot and sum per case. Best-effort: on library failure the
    字数 falls back to 0 (rows won't be dropped for a transient library error beyond
    what the bucket implies)."""
    pred = _ATTACHMENT_SIZE_BUCKETS.get(size)
    if pred is None:
        return rows
    row_ids = [_row_file_ids(r) for r in rows]
    all_ids = [i for ids in row_ids for i in ids]
    lengths: dict[str, int] = {}
    if all_ids:
        try:
            lengths = await asyncio.wait_for(resolve_text_lengths(all_ids), timeout=20)
        except Exception:  # noqa: BLE001 — never fail the list over the size filter
            logger.exception("resolve text_lengths failed (%d ids)", len(set(all_ids)))
    out: list[dict] = []
    for r, ids in zip(rows, row_ids):
        total = sum(lengths.get(i, 0) for i in ids)
        if pred(total):
            out.append(r)
    return out


def _dedup_by_question(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        q = (r.get("question") or "").strip()
        if not q:
            # Empty questions are not deduped — keep all.
            out.append(r)
            continue
        if q in seen:
            continue
        seen.add(q)
        out.append(r)
    return out


@router.post("", response_model=CasePage)
async def list_cases(
    filters: CaseFilter,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(get_current_user),
):
    if page > MAX_PAGE:
        raise HTTPException(
            400,
            f"页码不能超过 {MAX_PAGE}，请收窄时间范围或功能类型后再查",
        )

    sf = _source_filters(filters)
    schema = settings.case_schema
    sources = resolve_sources(
        filters.function_type or None,
        sub_function=sf.sub_function,
        draft_type=sf.draft_type,
    )
    offset = (page - 1) * page_size

    if not sources:
        return CasePage(total=0, total_capped=False, has_more=False, total_approx=False, items=[])

    per_source = offset + page_size
    if filters.dedup:
        per_source = max(per_source * 3, 50)
        per_source = min(per_source, MAX_PER_SOURCE_DEDUP)
    else:
        per_source = min(per_source, MAX_PER_SOURCE)

    # No precise COUNT (6 capped counts scanning live tables were the main cost).
    # Fetch only the list rows; the pager navigates by has_more (prev/next), and the
    # total is reported as a lower bound ("N+ 条") that firms up on the last page.
    groups = await asyncio.gather(*[
        _fetch_source_rows(s, schema=schema, sf=sf, per_source_limit=per_source)
        for s in sources
    ])

    merged = _merge_desc(list(groups))
    if filters.dedup:
        merged = _dedup_by_question(merged)
    if filters.attachment_size:
        merged = await _filter_by_attachment_size(merged, filters.attachment_size)

    page_rows = merged[offset: offset + page_size]
    has_more = len(merged) > offset + page_size

    await _hydrate_page(page_rows, schema=schema)
    await _resolve_qa_filenames(page_rows)

    # total = rows seen up to this page (lower bound). When has_more, it's capped
    # ("N+ 条") and the pager stays in has_more mode; on the last page it's exact.
    total = offset + len(page_rows)
    return CasePage(
        total=total,
        total_capped=has_more,
        has_more=has_more,
        total_approx=bool(filters.dedup),
        items=[_to_item(r) for r in page_rows],
    )


@router.post("/ids", response_model=CaseIdsResponse)
async def list_case_ids(
    filters: CaseFilter,
    _: CurrentUser = Depends(get_current_user),
):
    sf = _source_filters(filters)
    schema = settings.case_schema
    sources = resolve_sources(
        filters.function_type or None,
        sub_function=sf.sub_function,
        draft_type=sf.draft_type,
    )
    if not sources:
        return CaseIdsResponse(total=0, ids=[], capped=False)

    per_source = CASE_SELECTION_LIMIT
    if filters.dedup:
        per_source = min(CASE_SELECTION_LIMIT * 2, MAX_PER_SOURCE_DEDUP)

    groups = await asyncio.gather(*[
        _fetch_source_rows(s, schema=schema, sf=sf, per_source_limit=per_source)
        for s in sources
    ])
    merged = _merge_desc(list(groups))
    if filters.dedup:
        merged = _dedup_by_question(merged)
    if filters.attachment_size:
        merged = await _filter_by_attachment_size(merged, filters.attachment_size)

    ids = [r["task_id"] for r in merged[:CASE_SELECTION_LIMIT]]
    capped = len(merged) > CASE_SELECTION_LIMIT or any(
        len(g) >= per_source for g in groups
    )
    return CaseIdsResponse(total=len(ids), ids=ids, capped=capped)


async def _hydrate_page(rows: list[dict], *, schema: str) -> None:
    """Fill result_score / system_answer / attachment / has_file for page rows."""
    if not rows:
        return
    by_source: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_source[r["source"]].append(r)

    async def one_source(source: str, group: list[dict]) -> None:
        ids = [r["task_id"] for r in group]
        sql = build_page_hydrate_sql(schema, source)
        async with CaseSessionLocal() as session:
            hydrated = (await session.execute(text(sql), {"ids": ids})).mappings().all()
        by_id = {h["task_id"]: dict(h) for h in hydrated}
        for r in group:
            h = by_id.get(r["task_id"])
            if not h:
                continue
            r["result_score"] = h.get("result_score")
            r["system_answer"] = h.get("system_answer")
            if h.get("attachment") is not None:
                r["attachment"] = h["attachment"]
            if h.get("has_file") is not None:
                r["has_file"] = h["has_file"]

    await asyncio.gather(*(one_source(s, g) for s, g in by_source.items()))


async def _resolve_qa_filenames(rows: list[dict]) -> None:
    """Populate QA page rows with real file names via the library (best-effort)."""
    targets = [r for r in rows if r.get("kind") == "qa" and r.get("has_file")]
    if not targets:
        return

    async def one(r: dict) -> None:
        try:
            files = await resolve_oss_urls(r.get("project_id"), r.get("doc_ids"))
        except Exception:  # noqa: BLE001 — never fail the list over file names
            logger.exception("resolve QA filenames failed task=%s", r.get("task_id"))
            files = []
        r["file_names"] = [f["name"] for f in files if f.get("name")]

    try:
        await asyncio.wait_for(asyncio.gather(*(one(r) for r in targets)), timeout=10)
    except asyncio.TimeoutError:
        logger.warning("QA filename resolution timed out for %d rows", len(targets))


def _qa_attachment_display(names: list[str]) -> str | None:
    """Compact display for the 上传文件 cell: first name, plus 等N个 when multiple."""
    if not names:
        return None
    return names[0] if len(names) == 1 else f"{names[0]} 等{len(names)}个文件"


def _score_to_rating(score) -> str:
    # 0=差评, 1=好评, 2=未知, NULL=无反馈 → 未知/无反馈都显示为 none
    if score == 1:
        return "good"
    if score == 0:
        return "bad"
    return "none"


def _to_item(r) -> CaseItem:
    answer = recover_chinese_text(r.get("system_answer"))
    score = r.get("result_score")
    # QA: 展示 library 解析出的真实文件名；review: hydrate 给出文件名或任务名。
    if r.get("kind") == "qa":
        attachment = _qa_attachment_display(r.get("file_names") or [])
    else:
        attachment = recover_chinese_text(r.get("attachment"))
    return CaseItem(
        kind=r["kind"],
        task_id=r["task_id"],
        function_module=r["source"],
        sub_function=r.get("sub_function"),
        question=recover_chinese_text(r.get("question")),
        attachment=attachment,
        has_file=bool(r.get("has_file")),
        result_score=score,
        rating=_score_to_rating(score),
        system_answer=answer,
        is_empty_result=(answer is None or answer == ""),
        stance=None,
        channel_type=r.get("channel_type"),
        created_at=r["src_created"].isoformat() if r.get("src_created") is not None else None,
    )
