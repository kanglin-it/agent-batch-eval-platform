"""Global cap on concurrent Agent executions — distributed across workers.

The Agent runs under a single shared account, so the whole fleet (all tasks, all
uvicorn/gunicorn workers) must share ONE concurrency budget. A per-process
asyncio.Semaphore can't do that once you run multiple workers, so we use a Redis
sorted-set semaphore:

  - key = ZSET of {token: expiry_ts}
  - acquire: purge expired tokens, add our token, keep it iff our rank < limit
             (Redis runs each MULTI/EXEC atomically & single-threaded, so the
              rank check can't overshoot); otherwise remove + poll again
  - release: remove our token
  - crash-safe: a worker that dies leaves a token that simply expires (TTL)

TTL is sized just above the Agent chat timeout, since we only hold the slot around
the /api/chat call (which httpx bounds to chat_timeout). Falls back to a
per-process semaphore when Redis isn't configured (per-worker cap — better than
nothing) and fails OPEN on a Redis error (availability over a strict cap).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid

from app.core.config import settings

logger = logging.getLogger(__name__)

_KEY = "agent:concurrency"
_LIMIT = max(1, settings.agent_concurrency)
_TTL = settings.zhiexa_chat_timeout + 60      # slot is held only around /api/chat
_POLL_SECONDS = 0.2

_redis = None
_local_sem: asyncio.Semaphore | None = None


def _get_redis():
    global _redis
    if _redis is None and settings.redis_url:
        import redis.asyncio as aioredis  # lazy so a missing dep can't break import
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _get_local_sem() -> asyncio.Semaphore:
    global _local_sem
    if _local_sem is None:
        _local_sem = asyncio.Semaphore(_LIMIT)
    return _local_sem


async def _try_acquire(r, token: str) -> bool:
    now = time.time()
    async with r.pipeline(transaction=True) as pipe:
        pipe.zremrangebyscore(_KEY, "-inf", now)     # drop expired holders
        pipe.zadd(_KEY, {token: now + _TTL})         # claim a slot
        pipe.zrank(_KEY, token)                       # our position (by expiry ~ order)
        pipe.expire(_KEY, _TTL + 60)                  # keep the key from lingering forever
        _, _, rank, _ = await pipe.execute()
    if rank is not None and rank < _LIMIT:
        return True
    await r.zrem(_KEY, token)                          # over the cap → give it back
    return False


@contextlib.asynccontextmanager
async def agent_slot():
    """Hold one global Agent-concurrency slot for the duration of the block."""
    r = _get_redis()
    if r is None:
        async with _get_local_sem():                  # no Redis → per-worker cap
            yield
        return

    token = uuid.uuid4().hex
    acquired = False
    try:
        while True:
            try:
                if await _try_acquire(r, token):
                    acquired = True
                    break
            except Exception:  # noqa: BLE001 — Redis down must not freeze the run
                logger.exception("agent limiter Redis error; proceeding without global cap")
                yield
                return
            await asyncio.sleep(_POLL_SECONDS)
        yield
    finally:
        if acquired:
            with contextlib.suppress(Exception):
                await r.zrem(_KEY, token)
