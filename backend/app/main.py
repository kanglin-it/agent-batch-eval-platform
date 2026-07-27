import asyncio
import datetime as dt
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text

from app.api.routes import auth, cases, settings as settings_route, tasks
from app.core.config import settings
from app.db.case_session import CaseSessionLocal
from app.db.session import SessionLocal
from app.models.eval_task import EvalTask, TaskStatus
from app.services.case_source import SOURCE_ORDER, SourceFilters, build_list_source_sql

# Ensure our INFO app logs (e.g. [Coze] / [Compare]) actually print.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("app").setLevel(logging.INFO)

app = FastAPI(title="Agent 批量评测平台 API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

app.include_router(auth.router)
app.include_router(cases.router)
app.include_router(tasks.router)
app.include_router(settings_route.router)


@app.on_event("startup")
async def _reconcile_orphaned_tasks() -> None:
    """Background tasks don't survive restarts: any task still marked
    agent_running/comparing was interrupted. Mark it failed so it becomes
    retryable (per-case progress is already persisted, so retry resumes only the
    unfinished cases)."""
    async with SessionLocal() as db:
        orphans = (
            await db.execute(
                select(EvalTask).where(
                    EvalTask.status.in_([TaskStatus.agent_running, TaskStatus.comparing])
                )
            )
        ).scalars().all()
        for t in orphans:
            t.status = TaskStatus.failed
        if orphans:
            await db.commit()
            logging.getLogger("app").warning(
                "reconciled %d interrupted task(s) -> failed", len(orphans)
            )


async def _warm_up_case_sources() -> None:
    """Pre-open the read pool and warm PG/OS cache for the default 15-day window,
    so the user's *first* case-list query is already hot (it's otherwise several×
    slower cold). Best-effort and non-blocking: failures are logged, not fatal."""
    log = logging.getLogger("app")
    sf = SourceFilters(created_start=dt.date.today() - dt.timedelta(days=15))
    schema = settings.case_schema

    async def one(source: str) -> None:
        try:
            sql, params = build_list_source_sql(schema, source, filters=sf, per_source_limit=20)
            async with CaseSessionLocal() as session:
                await session.execute(text(sql), params)
        except Exception:  # noqa: BLE001 — warm-up must never break startup
            log.exception("warm-up query failed for source=%s", source)

    start = asyncio.get_event_loop().time()
    await asyncio.gather(*(one(s) for s in SOURCE_ORDER))
    log.info("case-source warm-up done (%.0fms)", (asyncio.get_event_loop().time() - start) * 1000)


@app.on_event("startup")
async def _warm_up_on_startup() -> None:
    # Run in the background so server readiness isn't delayed by the DB warm-up.
    asyncio.create_task(_warm_up_case_sources())


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}
