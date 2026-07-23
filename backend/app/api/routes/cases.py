"""Case management (用例管理页).

Cases are read directly from the history dataset tables in the PostgreSQL 'saas'
database: `t_history_qa_dataset` (问答型) and `t_history_review_dataset` (审查型),
merged into one list. These tables only contain FINISH tasks (failed/timeout tasks
are never exported), so the "去除失败任务" toggle is effectively always on.
"""
import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.case_session import get_case_db
from app.schemas.auth import CurrentUser
from app.schemas.case import CaseFilter, CaseIdsResponse, CaseItem, CasePage

router = APIRouter(prefix="/api/cases", tags=["cases"])

CASE_SELECTION_LIMIT = 500


def _union_cte() -> str:
    """Align the two differently-shaped dataset tables into one column set."""
    qa, review = settings.case_qa_table, settings.case_review_table
    return f"""
    WITH unioned AS (
        SELECT 'qa' AS kind, source, task_id, question, src_created, result_score,
               answer AS system_answer,
               doc_ids AS attachment,
               (doc_ids IS NOT NULL AND doc_ids <> '') AS has_file,
               NULL::jsonb AS stance
        FROM {qa}
        UNION ALL
        SELECT 'review' AS kind, source, task_id, question, src_created, result_score,
               NULL AS system_answer,
               original_file AS attachment,
               TRUE AS has_file,
               stance
        FROM {review}
    )"""


def _where(filters: CaseFilter) -> tuple[str, dict]:
    conds: list[str] = []
    params: dict = {}
    if filters.created_start:
        conds.append("src_created >= :created_start")
        params["created_start"] = filters.created_start
    if filters.created_end:
        conds.append("src_created <= :created_end")
        params["created_end"] = filters.created_end
    if filters.function_type:
        conds.append("source = :function_type")
        params["function_type"] = filters.function_type
    if filters.has_file is not None:
        conds.append("has_file = :has_file")
        params["has_file"] = filters.has_file
    if filters.keyword:
        conds.append("question ILIKE :keyword")
        params["keyword"] = f"%{filters.keyword}%"
    # 用户评价: 只有好评(1)/差评(0) 参与筛选; 未知(2)/无反馈(NULL) 不算好差评。
    if filters.user_rating in ("good", "bad"):
        conds.append("result_score = :rating_val")
        params["rating_val"] = 1 if filters.user_rating == "good" else 0
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    return where, params


def _select_body(filters: CaseFilter) -> str:
    """Core SELECT over the union, honouring the dedup (同用户问题) option."""
    where, _ = _where(filters)
    if filters.dedup:
        # Keep the latest row per identical question.
        return f"""
        SELECT DISTINCT ON (question) *
        FROM unioned {where}
        ORDER BY question, src_created DESC"""
    return f"SELECT * FROM unioned {where}"


@router.post("", response_model=CasePage)
async def list_cases(
    filters: CaseFilter,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_case_db),
    _: CurrentUser = Depends(get_current_user),
):
    _, params = _where(filters)
    body = _select_body(filters)
    cte = _union_cte()

    count_sql = text(f"{cte} SELECT count(*) FROM ({body}) AS c")
    total = (await db.execute(count_sql, params)).scalar_one()

    page_sql = text(
        f"{cte} SELECT * FROM ({body}) AS d ORDER BY d.src_created DESC LIMIT :limit OFFSET :offset"
    )
    rows = (
        await db.execute(page_sql, {**params, "limit": page_size, "offset": (page - 1) * page_size})
    ).mappings().all()

    return CasePage(total=total, items=[_to_item(r) for r in rows])


@router.post("/ids", response_model=CaseIdsResponse)
async def list_case_ids(
    filters: CaseFilter,
    db: AsyncSession = Depends(get_case_db),
    _: CurrentUser = Depends(get_current_user),
):
    """Return all task_ids matching the filter for cross-page "全部选中", capped at 500."""
    _, params = _where(filters)
    body = _select_body(filters)
    cte = _union_cte()

    total = (await db.execute(text(f"{cte} SELECT count(*) FROM ({body}) AS c"), params)).scalar_one()
    ids = (
        await db.execute(
            text(f"{cte} SELECT task_id FROM ({body}) AS d ORDER BY d.src_created DESC LIMIT :limit"),
            {**params, "limit": CASE_SELECTION_LIMIT},
        )
    ).scalars().all()
    return CaseIdsResponse(total=total, ids=list(ids), capped=total > CASE_SELECTION_LIMIT)


_RATING = {1: "good", 0: "bad"}


def _to_item(r) -> CaseItem:
    stance = r["stance"]
    if isinstance(stance, str):
        try:
            stance = json.loads(stance)
        except (TypeError, ValueError):
            stance = None
    answer = r["system_answer"]
    return CaseItem(
        kind=r["kind"],
        task_id=r["task_id"],
        function_module=r["source"],
        question=r["question"],
        attachment=r["attachment"],
        has_file=bool(r["has_file"]),
        result_score=r["result_score"],
        rating=_RATING.get(r["result_score"], "none"),
        system_answer=answer,
        is_empty_result=(answer is None or answer == "") if r["kind"] == "qa" else None,
        stance=stance,
        created_at=r["src_created"].isoformat() if r["src_created"] is not None else None,
    )
