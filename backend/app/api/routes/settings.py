"""Runtime platform settings (admin only).

Currently exposes the login-session lifetime so it can be changed at runtime
without a redeploy. Changing it affects NEW logins; already-issued tokens keep
their original expiry (stateless JWT).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_superuser
from app.db.session import get_db
from app.schemas.auth import CurrentUser
from app.schemas.setting import LoginTtlResponse, UpdateLoginTtlRequest
from app.services.settings_service import get_login_ttl, set_login_ttl

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/login-ttl", response_model=LoginTtlResponse)
async def read_login_ttl(db: AsyncSession = Depends(get_db), _: CurrentUser = Depends(get_current_superuser)):
    seconds = await get_login_ttl(db)
    return LoginTtlResponse(login_ttl_seconds=seconds, login_ttl_days=round(seconds / 86400, 2))


@router.put("/login-ttl", response_model=LoginTtlResponse)
async def update_login_ttl(
    body: UpdateLoginTtlRequest,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_superuser),
):
    if body.login_ttl_days is not None:
        seconds = int(body.login_ttl_days * 86400)
    elif body.login_ttl_seconds is not None:
        seconds = body.login_ttl_seconds
    else:
        raise HTTPException(400, "请提供 login_ttl_seconds 或 login_ttl_days")

    try:
        seconds = await set_login_ttl(db, seconds)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return LoginTtlResponse(login_ttl_seconds=seconds, login_ttl_days=round(seconds / 86400, 2))
