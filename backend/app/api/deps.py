from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt import InvalidTokenError

from app.core.security import decode_access_token
from app.schemas.auth import CurrentUser

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


async def get_current_user(token: str = Depends(oauth2_scheme)) -> CurrentUser:
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="登录已失效，请重新登录",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
    except InvalidTokenError:
        raise cred_exc
    if payload.get("sub") is None:
        raise cred_exc
    return CurrentUser(
        id=int(payload["sub"]),
        username=payload.get("username", ""),
        is_staff=payload.get("is_staff", False),
        is_superuser=payload.get("is_superuser", False),
    )


async def get_current_superuser(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Restrict an endpoint to Django superusers (used for platform settings)."""
    if not current.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要超级管理员权限")
    return current
