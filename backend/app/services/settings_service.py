"""Resolve and update runtime settings.

Effective value = platform_setting row (if present) -> env default.
Login TTL is read once per login (infrequent), so no caching is needed.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.platform_setting import LOGIN_TTL_SECONDS, PlatformSetting

# Guardrails for a manually-set login lifetime: 5 minutes .. 365 days.
MIN_LOGIN_TTL = 300
MAX_LOGIN_TTL = 31536000


async def get_login_ttl(db: AsyncSession) -> int:
    """Effective login-session lifetime in seconds (DB override or env default)."""
    row = await db.get(PlatformSetting, LOGIN_TTL_SECONDS)
    if row is None:
        return settings.login_ttl_seconds
    try:
        return int(row.value)
    except (TypeError, ValueError):
        return settings.login_ttl_seconds


async def set_login_ttl(db: AsyncSession, seconds: int) -> int:
    """Persist a new login-session lifetime (upsert). Returns the stored value."""
    if not (MIN_LOGIN_TTL <= seconds <= MAX_LOGIN_TTL):
        raise ValueError(f"login_ttl_seconds 必须在 {MIN_LOGIN_TTL}~{MAX_LOGIN_TTL} 秒之间")
    row = await db.get(PlatformSetting, LOGIN_TTL_SECONDS)
    if row is None:
        row = PlatformSetting(key=LOGIN_TTL_SECONDS, value=str(seconds))
        db.add(row)
    else:
        row.value = str(seconds)
    await db.commit()
    return seconds
