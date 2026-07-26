"""Hydrate selected cases from live SaaS task tables.

At task-creation time we only have the selected task_ids. Before the Agent can
rerun them we pull each case's question / files / stance / historical baseline
from the same 6 live sources used by the case list
(`scripts/export_history_seed_sql.py`).

Each source query is filtered by `task_id = ANY(:ids)` so we never scan full tables.
"""
import asyncio
import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.case_source import build_hydrate_sql
from app.services.library_client import resolve_oss_urls
from app.utils.text_fix import recover_chinese_text


async def fetch_case_data(db: AsyncSession, task_ids: list[str]) -> dict[str, dict]:
    """Return {task_id: {source, question, files, stance, baseline_answer}}."""
    if not task_ids:
        return {}

    sql = text(build_hydrate_sql(schema=settings.case_schema))
    rows = (await db.execute(sql, {"ids": task_ids})).mappings().all()

    out: dict[str, dict] = {}
    qa_refs: list[tuple[str, str | None, str | None]] = []  # (task_id, project_id, doc_ids)
    for r in rows:
        if r["kind"] == "qa":
            # QA files are referenced by doc_ids/project_id -> resolve to OSS via library.
            files = []
            stance = None
            baseline = recover_chinese_text(r["system_answer"])
            qa_refs.append((r["task_id"], r.get("project_id"), r.get("attachment")))
        else:
            files = []
            orig = _as_json(r["original_file"])
            if isinstance(orig, dict) and orig.get("url"):
                files.append({"url": orig["url"], "name": orig.get("name") or ""})
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

        out[r["task_id"]] = {
            "source": r["source"],
            "question": recover_chinese_text(r["question"]) or "",
            "files": files,
            "stance": stance,
            "baseline_answer": baseline or "",
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


def _as_json(v):
    if v is None or isinstance(v, (list, dict)):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return None
    return None
