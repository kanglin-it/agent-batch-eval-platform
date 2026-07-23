import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.security import create_access_token
from app.schemas.auth import CurrentUser, LoginRequest, TokenResponse
from app.services.settings_service import get_login_ttl

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    """Authenticate via the ops-platform password API (fast — no local hashing/DB).

    The upstream returns {"code": 200, ...} on success; anything else is a failure.
    """
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

    ttl_seconds = get_login_ttl()
    token = create_access_token(
        subject=body.phone,
        ttl_seconds=ttl_seconds,
        extra={
            "username": body.phone,
            "phone": body.phone,
            "is_staff": True,
            "is_superuser": True,
        },
    )
    return TokenResponse(access_token=token, expires_in=ttl_seconds)


@router.get("/me", response_model=CurrentUser)
async def me(current: CurrentUser = Depends(get_current_user)):
    return current
