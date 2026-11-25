"""Shared FastAPI dependencies wired to app.state so tests can inject fakes."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import bearer, decode_access_token
from .models import AuditLog, User


def get_settings(request: Request):
    return request.app.state.settings


def get_auth_deps(request: Request):
    return request.app.state.auth_deps


def get_router(request: Request):
    return request.app.state.provider_router


def get_rate_limiter(request: Request):
    return request.app.state.rate_limiter


async def get_session(request: Request):
    async with request.app.state.session_factory() as session:
        yield session


async def get_current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    credentials = await bearer(request)
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token"
        )
    settings = request.app.state.settings
    user_id = decode_access_token(credentials.credentials, settings)
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found"
        )
    return user


async def audit(session: AsyncSession, user_id: str | None, action: str, detail: str = ""):
    session.add(AuditLog(user_id=user_id, action=action, detail=detail))
