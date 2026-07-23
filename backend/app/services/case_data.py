"""Hydrate selected cases from live SaaS task tables.

At task-creation time we only have the selected task_ids. Before the Agent can
rerun them we pull each case's question / files / stance / historical baseline
from the same 6 live sources used by the case list
(`scripts/export_history_seed_sql.py`).

Each source query is filtered by `task_id = ANY(:ids)` so we never scan full tables.
"""
import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.case_source import build_hydrate_sql


async def fetch_case_data(db: AsyncSession, task_ids: list[str]) -> dict[str, dict]:
    """Return {task_id: {source, question, files, stance, baseline_answer}}."""
    if not task_ids:
        return {}

    sql = text(build_hydrate_sql(schema=settings.case_schema))
    rows = (await db.execute(sql, {"ids": task_ids})).mappings().all()

    out: dict[str, dict] = {}
    for r in rows:
        if r["kind"] == "qa":
            files = [r["attachment"]] if r["attachment"] else []
            stance = None
            baseline = r["system_answer"]
        else:
            files = []
            orig = _as_json(r["original_file"])
            if isinstance(orig, dict) and orig.get("url"):
                files.append(orig["url"])
            elif isinstance(orig, str) and orig:
                files.append(orig)
            refs = _as_json(r["reference_files"])
            if isinstance(refs, list):
                for item in refs:
                    if isinstance(item, dict) and item.get("url"):
                        files.append(item["url"])
                    elif isinstance(item, str):
                        files.append(item)
            stance_val = _as_json(r["stance"])
            stance = json.dumps(stance_val, ensure_ascii=False) if stance_val is not None else None
            baseline = r["detail_annotated_file"]

        out[r["task_id"]] = {
            "source": r["source"],
            "question": r["question"] or "",
            "files": files,
            "stance": stance,
            "baseline_answer": baseline,
        }
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
