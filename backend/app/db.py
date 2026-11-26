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
    kwargs: dict = {"future": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    elif database_url.startswith("postgresql+asyncpg"):
        # asyncpg does not understand libpq query options such as sslmode or
        # channel_binding (Neon/Render connection strings carry them).
        from sqlalchemy.engine import make_url

        url = make_url(database_url)
        query = dict(url.query)
        sslmode = query.pop("sslmode", None)
        query.pop("channel_binding", None)
        url = url.set(query=query)
        if sslmode in ("require", "verify-ca", "verify-full") or "neon.tech" in (url.host or ""):
            kwargs["connect_args"] = {"ssl": "require"}
        kwargs["pool_pre_ping"] = True
        kwargs["pool_recycle"] = 300
        database_url = url.render_as_string(hide_password=False)
    return create_async_engine(database_url, **kwargs)


def make_session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db(engine) -> None:
    from . import models  # noqa: F401  (register tables)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
