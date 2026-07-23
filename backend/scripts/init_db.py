"""Create this platform's own tables (dev/bootstrap helper).

Creates ONLY the tables this platform owns — eval_task, eval_task_case — and
never touches the Django-owned user table. Runtime settings (e.g. login TTL) are
stored in a JSON config file, not the database, so there is no settings table.
For real deployments prefer Alembic migrations; this is a convenience for local setup.

Usage:
    cd backend && python -m scripts.init_db
"""
import asyncio

from app.db.session import Base, engine  # noqa: F401
from app.models.eval_task import EvalTask, EvalTaskCase  # noqa: F401

OWNED_TABLES = [EvalTask.__table__, EvalTaskCase.__table__]


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=OWNED_TABLES)
    print("Created tables:", ", ".join(t.name for t in OWNED_TABLES))


if __name__ == "__main__":
    asyncio.run(main())
