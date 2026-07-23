"""Case management (用例管理页).

The list is sourced from the SaaS user-behavior detail table. The concrete query
depends on how that data is exposed (direct DB read / internal API / data warehouse),
which is still TBD — so this module returns a typed, stubbed response that the
frontend can build against, with the filter/dedup/exclude-failed contract fixed.
"""
from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_user
from app.schemas.auth import CurrentUser
from app.schemas.case import CaseFilter, CasePage

router = APIRouter(prefix="/api/cases", tags=["cases"])

CASE_SELECTION_LIMIT = 500


@router.post("", response_model=CasePage)
async def list_cases(
    filters: CaseFilter,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(get_current_user),
):
    # TODO: replace with real query against the SaaS user-behavior source.
    #   - apply filters (created range, function_type, has_file, rating, keyword)
    #   - apply data-processing: dedup (same question) / exclude_failed (timeout/error)
    #   - paginate
    return CasePage(total=0, items=[])
