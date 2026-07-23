import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import CurrentUser, LoginRequest, TokenResponse
from app.services.settings_service import get_login_ttl

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Verify the password via the ops-platform API (fast — no local hashing), then
    read user info from t_operation_user. Superuser is granted only to phones in the
    admin allowlist (config [auth] admin_phones).
    """
    # 1) Verify credentials via the ops-platform password API.
    try:
        async with httpx.AsyncClient(timeout=settings.operation_login_timeout) as client:
            resp = await client.post(
                settings.operation_login_url,
                json={"phone_number": body.phone, "password": body.password},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"登录服务不可用: {exc}")

    try:
        data = resp.json()
    except ValueError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "登录服务返回异常")

    if data.get("code") != 200:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, data.get("message") or "手机号或密码错误")

    # 2) Load user info from the ops-platform user table (fast; no hashing).
    result = await db.execute(select(User).where(User.phone == body.phone))
    user = result.scalar_one_or_none()

    is_admin = body.phone in settings.admin_phones
    username = user.username if user else body.phone
    user_id = user.id if user else 0
    is_staff = bool(user.is_staff) if user else False

    ttl_seconds = get_login_ttl()
    token = create_access_token(
        subject=str(user_id),
        ttl_seconds=ttl_seconds,
        extra={
            "username": username,
            "phone": body.phone,
            "is_staff": is_staff,
            "is_superuser": is_admin,
        },
    )
    return TokenResponse(access_token=token, expires_in=ttl_seconds)


@router.get("/me", response_model=CurrentUser)
async def me(current: CurrentUser = Depends(get_current_user)):
    return current
