"""Scheduled-task runner (top-of-hour poll, multi-worker safe).

Tasks created with a future `scheduled_at` sit in status='scheduled'. A poll runs
at each top of the hour and launches the ones that are due.

Two independent guards make this safe across many worker pods:
  1. Redis lock keyed by the hour → only ONE worker actually polls each hour
     (avoids every pod hammering the DB). Best-effort: if Redis is down/unset,
     we still poll (correctness is guard #2, not this lock).
  2. Atomic DB claim: `UPDATE ... WHERE status='scheduled' AND scheduled_at<=now()
     RETURNING id` flips each due row exactly once, so even if two workers poll
     concurrently, each task is launched by only one of them.

Overdue tasks (a poll missed while pods were down) are caught on the next poll
because the claim uses `scheduled_at <= now()`.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import text

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.task_runner import run_task

logger = logging.getLogger(__name__)

_LOCK_TTL = 3900  # a bit over an hour; the per-hour key auto-expires
_redis = None


def _get_redis():
    global _redis
    if _redis is None and settings.redis_url:
        import redis.asyncio as aioredis  # lazy
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _seconds_to_next_hour() -> float:
    now = dt.datetime.now(dt.timezone.utc)
    nxt = now.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
    return (nxt - now).total_seconds()


async def _poll_due_tasks() -> None:
    # Guard 1: one worker per hour (best-effort).
    r = _get_redis()
    if r is not None:
        key = dt.datetime.now(dt.timezone.utc).strftime("scheduler:poll:%Y%m%d%H")
        try:
            if not await r.set(key, "1", nx=True, ex=_LOCK_TTL):
                return  # another worker owns this hour's poll
        except Exception:  # noqa: BLE001 — Redis hiccup: fall through, guard 2 covers us
            logger.exception("scheduler redis lock failed; polling anyway")

    # Guard 2: atomic claim — each due row flips to agent_running exactly once.
    async with SessionLocal() as db:
        # is_delete guard: a cancelled (logically-deleted) scheduled task keeps
        # status='scheduled', so it must be excluded here or it would still fire.
        result = await db.execute(text(
            "UPDATE eval_task SET status='agent_running', updated_at=now() "
            "WHERE status='scheduled' AND scheduled_at <= now() AND is_delete = false "
            "RETURNING id"
        ))
        ids = [row[0] for row in result.fetchall()]
        await db.commit()

    for tid in ids:
        logger.info("scheduler launching due task id=%s", tid)
        asyncio.create_task(run_task(tid))


async def _safe_poll() -> None:
    try:
        await _poll_due_tasks()
    except Exception:  # noqa: BLE001 — never let the loop die
        logger.exception("scheduler poll failed")


async def scheduler_loop() -> None:
    """Startup catch-up poll, then one poll at each top of the hour."""
    await asyncio.sleep(5)          # let the app settle; catch tasks overdue after a deploy
    await _safe_poll()
    while True:
        await asyncio.sleep(_seconds_to_next_hour() + 2)  # wake just after :00
        await _safe_poll()
