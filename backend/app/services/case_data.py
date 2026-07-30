"""Hydrate selected cases from live SaaS task tables.

At task-creation time we only have the selected task_ids. Before the Agent can
rerun them we pull each case's question / files / stance / historical baseline
from the same 6 live sources used by the case list
(`scripts/export_history_seed_sql.py`).

Each source query is filtered by `task_id = ANY(:ids)` so we never scan full tables.
"""
import asyncio
import json
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.baseline_links import build_baseline_jump_url
from app.services.case_source import (
    COZE_DESCRIBE_BY_SOURCE,
    build_hydrate_sql,
    build_jump_meta_sql,
)
from app.services.library_client import resolve_oss_urls
from app.utils.text_fix import recover_chinese_text


async def fetch_case_data(db: AsyncSession, task_ids: list[str]) -> dict[str, dict]:
    """Return {task_id: {source, question, files, stance, baseline_*, ...}}."""
    if not task_ids:
        return {}

    sql = text(build_hydrate_sql(schema=settings.case_schema))
    rows = (await db.execute(sql, {"ids": task_ids})).mappings().all()

    out: dict[str, dict] = {}
    qa_refs: list[tuple[str, str | None, str | None]] = []  # (task_id, project_id, doc_ids)
    for r in rows:
        file_id = None
        file_name = None
        if r["kind"] == "qa":
            # QA files are referenced by doc_ids/project_id -> resolve to OSS via library.
            files = []
            stance = None
            baseline = recover_chinese_text(r["system_answer"])
            qa_refs.append((r["task_id"], r.get("project_id"), r.get("attachment")))
        else:
            files = []
            orig = _as_json(r["original_file"])
            if isinstance(orig, dict):
                file_id = (orig.get("file_id") or "").strip() or None
                file_name = (orig.get("name") or "").strip() or None
                if orig.get("url"):
                    files.append({
                        "url": orig["url"],
                        "name": orig.get("name") or "",
                        "file_id": file_id,
                    })
            elif isinstance(orig, str) and orig.startswith(("http://", "https://")):
                files.append({"url": orig, "name": ""})
            refs = _as_json(r["reference_files"])
            if isinstance(refs, list):
                for item in refs:
                    if isinstance(item, dict) and item.get("url"):
                        files.append({"url": item["url"], "name": item.get("name") or ""})
                    elif isinstance(item, str) and item.startswith(("http://", "https://")):
                        files.append({"url": item, "name": ""})
            stance_val = _as_json(r["stance"])
            stance = json.dumps(stance_val, ensure_ascii=False) if stance_val is not None else None
            # Coze answer_old: 合同=结果卡片摘要，文件审查=final_result（无则 review_report）
            # 不再用 detail_annotated_file OSS 链接。
            baseline = recover_chinese_text(r.get("system_answer"))
            if not file_name:
                file_name = (recover_chinese_text(r.get("question")) or "").strip() or None

        source = r["source"]
        tid = r["task_id"]
        out[tid] = {
            "source": source,
            "question": recover_chinese_text(r["question"]) or "",
            "files": files,
            "stance": stance,
            "baseline_answer": baseline or "",
            "baseline_coze_url": (r.get("baseline_coze_url") or "").strip() or None,
            "baseline_jump_url": build_baseline_jump_url(
                source,
                tid,
                user_id=r.get("source_user_id"),
                file_id=file_id,
                file_name=file_name,
            ),
        }

    # Resolve QA files (doc_ids/project_id -> OSS urls) concurrently via the library.
    if qa_refs:
        resolved = await asyncio.gather(
            *(resolve_oss_urls(pid, dids) for (_, pid, dids) in qa_refs)
        )
        for (tid, _, _), fs in zip(qa_refs, resolved):
            if tid in out and fs:
                out[tid]["files"] = fs

    return out


async def fetch_baseline_coze_urls(
    db: AsyncSession, pairs: list[tuple[str, str | None]]
) -> dict[str, str]:
    """Map source task_id -> t_coze_log.debug_url (latest matching row per task).

    `pairs` = [(task_id, source), ...]. The right row is the 主流程 one, identified by
    describe per source (COZE_DESCRIBE_BY_SOURCE); task_ids are grouped by their
    describe filter so each group runs one query. Sources without a mapped describe
    fall back to "latest non-empty" (no describe filter)."""
    if not pairs:
        return {}
    schema = settings.case_schema
    by_desc: dict[str | None, list[str]] = defaultdict(list)
    for tid, source in pairs:
        by_desc[COZE_DESCRIBE_BY_SOURCE.get(source)].append(tid)

    out: dict[str, str] = {}
    for desc, ids in by_desc.items():
        cond = "AND cl.describe = :desc" if desc else ""
        sql = text(f"""
            SELECT DISTINCT ON (cl.task_id) cl.task_id, cl.debug_url
              FROM {schema}.t_coze_log cl
             WHERE cl.task_id = ANY(:ids)
               AND cl.debug_url IS NOT NULL AND btrim(cl.debug_url) <> ''
               {cond}
             ORDER BY cl.task_id, cl.id DESC
        """)
        params: dict = {"ids": ids}
        if desc:
            params["desc"] = desc
        rows = (await db.execute(sql, params)).mappings().all()
        for r in rows:
            if r.get("debug_url"):
                out[r["task_id"]] = r["debug_url"].strip()
    return out


async def fetch_baseline_jump_urls(db: AsyncSession, task_ids: list[str]) -> dict[str, str]:
    """Map source task_id -> 旧答案跳转链接。"""
    if not task_ids:
        return {}
    sql = text(build_jump_meta_sql(schema=settings.case_schema))
    rows = (await db.execute(sql, {"ids": task_ids})).mappings().all()
    out: dict[str, str] = {}
    for r in rows:
        url = build_baseline_jump_url(
            r["source"],
            r["task_id"],
            user_id=r.get("user_id"),
            file_id=r.get("file_id"),
            file_name=r.get("file_name"),
        )
        if url:
            out[r["task_id"]] = url
    return out


def _as_json(v):
    if v is None or isinstance(v, (list, dict)):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return None
    return None
