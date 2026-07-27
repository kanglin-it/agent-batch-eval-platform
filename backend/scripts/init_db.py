"""Create this platform's own tables (dev/bootstrap helper).

Creates ONLY the tables this platform owns — eval_task, eval_task_case — and
never touches the Django-owned user table. Runtime settings (e.g. login TTL) are
stored in a JSON config file, not the database, so there is no settings table.
For real deployments prefer Alembic migrations; this is a convenience for local setup.

Usage:
    cd backend && python -m scripts.init_db
"""
import asyncio

from sqlalchemy import text

from app.db.session import Base, engine  # noqa: F401
from app.models.eval_task import EvalTask, EvalTaskCase  # noqa: F401

OWNED_TABLES = [EvalTask.__table__, EvalTaskCase.__table__]


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=OWNED_TABLES)
        # Lightweight upgrades for existing local DBs (create_all won't ALTER).
        await conn.execute(text(
            "ALTER TABLE eval_task_case ADD COLUMN IF NOT EXISTS source VARCHAR(32)"
        ))
        await conn.execute(text(
            "ALTER TABLE eval_task_case ADD COLUMN IF NOT EXISTS agent_file_result TEXT"
        ))
        await conn.execute(text(
            "ALTER TABLE eval_task_case ADD COLUMN IF NOT EXISTS coze_exec_url TEXT"
        ))
        await conn.execute(text(
            "ALTER TABLE eval_task_case ADD COLUMN IF NOT EXISTS agent_conversation_id VARCHAR(100)"
        ))
        await conn.execute(text(
            "ALTER TABLE eval_task_case ADD COLUMN IF NOT EXISTS agent_task_url TEXT"
        ))
        await conn.execute(text(
            "ALTER TABLE eval_task ADD COLUMN IF NOT EXISTS creator_phone VARCHAR(30)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_eval_task_creator_phone ON eval_task (creator_phone)"
        ))
    print("Created tables:", ", ".join(t.name for t in OWNED_TABLES))


if __name__ == "__main__":
    asyncio.run(main())
