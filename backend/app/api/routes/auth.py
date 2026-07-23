from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.security import create_access_token, verify_password
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import CurrentUser, LoginRequest, TokenResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Authenticate against the Django ops-backend user table.

    We only READ the user row and verify the plaintext against Django's stored
    hash — no Django runtime and no SECRET_KEY required.
    """
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "账号不存在或已被禁用")
    if not verify_password(body.password, user.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")

    token = create_access_token(
        subject=str(user.id),
        extra={
            "username": user.username,
            "is_staff": user.is_staff,
            "is_superuser": user.is_superuser,
        },
    )
    return TokenResponse(access_token=token)


@router.get("/me", response_model=CurrentUser)
async def me(current: CurrentUser = Depends(get_current_user)):
    return current
