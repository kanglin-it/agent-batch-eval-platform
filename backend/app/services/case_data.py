"""Hydrate selected cases from the PG history dataset tables.

At task-creation time we only have the selected task_ids. Before the Agent can
rerun them we must pull each case's question / files / stance / historical baseline
out of `t_history_qa_dataset` / `t_history_review_dataset`.

Per type:
  QA     -> question; baseline = answer;                 files = [doc_ids]
  review -> question; baseline = detail_annotated_file;  files = [original_file, *reference_files]; stance
"""
import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings


async def fetch_case_data(db: AsyncSession, task_ids: list[str]) -> dict[str, dict]:
    """Return {task_id: {source, question, files, stance, baseline_answer}}."""
    if not task_ids:
        return {}
    qa, review = settings.case_qa_table, settings.case_review_table
    sql = text(f"""
        SELECT 'qa' AS kind, source, task_id, question,
               answer AS baseline_answer,
               doc_ids AS doc_ids,
               NULL AS original_file, NULL::jsonb AS reference_files, NULL::jsonb AS stance
        FROM {qa} WHERE task_id = ANY(:ids)
        UNION ALL
        SELECT 'review' AS kind, source, task_id, question,
               detail_annotated_file AS baseline_answer,
               NULL AS doc_ids,
               original_file, reference_files, stance
        FROM {review} WHERE task_id = ANY(:ids)
    """)
    rows = (await db.execute(sql, {"ids": task_ids})).mappings().all()

    out: dict[str, dict] = {}
    for r in rows:
        if r["kind"] == "qa":
            files = [r["doc_ids"]] if r["doc_ids"] else []
            stance = None
        else:
            files = [r["original_file"]] if r["original_file"] else []
            refs = _as_json(r["reference_files"])
            if isinstance(refs, list):
                files += refs
            stance_val = _as_json(r["stance"])
            stance = json.dumps(stance_val, ensure_ascii=False) if stance_val is not None else None

        out[r["task_id"]] = {
            "source": r["source"],
            "question": r["question"] or "",
            "files": files,
            "stance": stance,
            "baseline_answer": r["baseline_answer"],
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
