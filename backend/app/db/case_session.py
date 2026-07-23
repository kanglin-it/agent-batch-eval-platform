"""Connection to the case-data database (PostgreSQL 'saas').

Read-only usage: 用例管理页 queries the history dataset tables here. Kept separate
from the login database so the two data sources stay decoupled.
"""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

case_engine = create_async_engine(settings.case_database_url, pool_pre_ping=True, future=True)
CaseSessionLocal = async_sessionmaker(case_engine, expire_on_commit=False, class_=AsyncSession)


async def get_case_db() -> AsyncGenerator[AsyncSession, None]:
    async with CaseSessionLocal() as session:
        yield session
