"""Async database setup. SQLite (aiosqlite) for dev/tests, PostgreSQL
(asyncpg) in production via DATABASE_URL."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str):
    kwargs = {"future": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_async_engine(database_url, **kwargs)


def make_session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db(engine) -> None:
    from . import models  # noqa: F401  (register tables)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
