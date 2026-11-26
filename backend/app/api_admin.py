"""Admin dashboard stats. Admin = user email listed in ADMIN_EMAILS env var."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select

from .deps import get_current_user, get_session
from .models import Conversation, GeneratedImage, Message, User

router = APIRouter(prefix="/v1/admin", tags=["admin"])


def _admin_emails(settings) -> set[str]:
    raw = getattr(settings, "admin_emails", "") or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


@router.get("/stats")
async def admin_stats(
    request: Request,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    settings = request.app.state.settings
    if user.email.lower() not in _admin_emails(settings):
        raise HTTPException(status_code=403, detail="غير مصرح لك بالوصول إلى لوحة المشرف")

    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    async def count(stmt):
        return (await session.execute(stmt)).scalar_one()

    users_total = await count(select(func.count()).select_from(User))
    users_24h = await count(
        select(func.count()).select_from(User).where(User.created_at >= day_ago)
    )
    users_7d = await count(
        select(func.count()).select_from(User).where(User.created_at >= week_ago)
    )
    convs_total = await count(select(func.count()).select_from(Conversation))
    msgs_total = await count(select(func.count()).select_from(Message))
    msgs_24h = await count(
        select(func.count()).select_from(Message).where(Message.created_at >= day_ago)
    )
    images_total = await count(select(func.count()).select_from(GeneratedImage))

    by_model_rows = (
        await session.execute(
            select(Message.provider, Message.model, func.count())
            .where(Message.role == "assistant")
            .group_by(Message.provider, Message.model)
            .order_by(func.count().desc())
        )
    ).all()
    by_model = [
        {
            "provider": p or "",
            "model": m or "",
            "messages": c,
        }
        for p, m, c in by_model_rows
    ]

    recent_rows = (
        await session.execute(
            select(User).order_by(User.created_at.desc()).limit(10)
        )
    ).scalars().all()
    recent_users = [
        {
            "email": u.email,
            "display_name": u.display_name,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in recent_rows
    ]

    return {
        "users_total": users_total,
        "users_24h": users_24h,
        "users_7d": users_7d,
        "conversations_total": convs_total,
        "messages_total": msgs_total,
        "messages_24h": msgs_24h,
        "images_total": images_total,
        "by_model": by_model,
        "recent_users": recent_users,
    }
