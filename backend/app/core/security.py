"""Authentication helpers.

Two responsibilities:
  1. Verify a plaintext password against a Django password hash (read-only reuse
     of the ops backend's user table). Django hashes are one-way and self-describing
     (`algorithm$iterations$salt$hash`), so verification needs NO Django SECRET_KEY.
  2. Issue / decode this platform's own JWT access token.
"""
import datetime as dt

import jwt
from passlib.context import CryptContext

from app.core.config import settings

# Covers Django's default and common hashers. Add/remove to match your ops backend.
pwd_context = CryptContext(
    schemes=[
        "django_pbkdf2_sha256",  # Django default
        "django_pbkdf2_sha1",
        "django_bcrypt_sha256",
        "django_bcrypt",
        "django_argon2",
    ],
    deprecated="auto",
)


def verify_password(plain: str, django_hash: str) -> bool:
    """Return True if `plain` matches the stored Django password hash."""
    if not django_hash:
        return False
    try:
        return pwd_context.verify(plain, django_hash)
    except ValueError:
        # Unknown/unsupported hash scheme in the stored value.
        return False


def create_access_token(*, subject: str, extra: dict | None = None) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + dt.timedelta(hours=settings.jwt_expire_hours),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
