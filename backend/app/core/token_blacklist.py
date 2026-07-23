"""In-memory JWT revoke list (jti -> exp unix ts).

Enough for single-process local/dev and small deployments. Tokens stay rejected
until their natural expiry, then the entry is dropped.
"""
from __future__ import annotations

import time

_revoked: dict[str, float] = {}


def revoke(jti: str, exp: float | None = None) -> None:
    if not jti:
        return
    _revoked[jti] = float(exp) if exp is not None else time.time() + 86400 * 30
    _purge()


def is_revoked(jti: str | None) -> bool:
    if not jti:
        return False
    _purge()
    return jti in _revoked


def _purge() -> None:
    now = time.time()
    dead = [k for k, exp in _revoked.items() if exp <= now]
    for k in dead:
        _revoked.pop(k, None)
