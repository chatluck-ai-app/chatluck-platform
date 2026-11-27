"""Plans and per-user daily usage. Limits live in the database so an admin can
change them without a deploy. These are honest quotas, not "unlimited": the free
providers behind the app have real limits."""
from __future__ import annotations

import time

from fastapi import HTTPException, status
from sqlalchemy import select

from .models import PlanLimit, UsageRecord, User

KINDS = ("message", "heavy", "image")
DEFAULT_PLANS = [
    ("free", "Free", 50, 10, 5),
    ("pro", "Pro", 300, 60, 40),
    ("premium", "Premium", 1000, 200, 100),
]
_FIELD = {"message": "daily_messages", "heavy": "daily_heavy", "image": "daily_images"}
_MSG = {
    "message": "وصلت للحد اليومي للرسائل في باقتك. يتجدد بكرة.",
    "heavy": "وصلت للحد اليومي لتحليل الصور والبحث في باقتك. يتجدد بكرة.",
    "image": "وصلت للحد اليومي لإنشاء الصور في باقتك. يتجدد بكرة.",
}


def today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


async def seed_plans(session) -> None:
    existing = {p for (p,) in (await session.execute(select(PlanLimit.plan))).all()}
    for plan, label, m, h, i in DEFAULT_PLANS:
        if plan not in existing:
            session.add(
                PlanLimit(plan=plan, label=label, daily_messages=m, daily_heavy=h, daily_images=i)
            )
    await session.commit()


async def get_limits(session, plan: str) -> PlanLimit:
    row = await session.get(PlanLimit, plan)
    if row is None:
        row = await session.get(PlanLimit, "free")
    if row is None:  # tables seeded at startup; this is a safety net for tests
        await seed_plans(session)
        row = await session.get(PlanLimit, plan) or await session.get(PlanLimit, "free")
    return row


async def usage_today(session, user_id: str) -> dict[str, int]:
    rows = (
        await session.execute(
            select(UsageRecord.kind, UsageRecord.count).where(
                UsageRecord.user_id == user_id, UsageRecord.day == today()
            )
        )
    ).all()
    out = {k: 0 for k in KINDS}
    out.update({k: c for k, c in rows})
    return out


async def consume(session, user: User, kind: str) -> None:
    """Count one use of `kind` today; raise 429 (Arabic) when over the plan limit.
    Caller commits."""
    limits = await get_limits(session, user.plan or "free")
    limit = getattr(limits, _FIELD[kind])
    row = (
        await session.execute(
            select(UsageRecord).where(
                UsageRecord.user_id == user.id,
                UsageRecord.day == today(),
                UsageRecord.kind == kind,
            )
        )
    ).scalar_one_or_none()
    used = row.count if row else 0
    if used >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=_MSG[kind],
            headers={"X-Plan": user.plan or "free", "X-Limit": str(limit)},
        )
    if row is None:
        session.add(UsageRecord(user_id=user.id, day=today(), kind=kind, count=1))
    else:
        row.count = used + 1
    await session.flush()
