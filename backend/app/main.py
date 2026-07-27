import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api.routes import auth, cases, settings as settings_route, tasks
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.eval_task import EvalTask, TaskStatus

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


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}
