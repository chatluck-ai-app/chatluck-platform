"""Plan/usage, guest mode, account deletion, privacy page, admin plan editing."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, select

from .api_admin import _admin_emails
from .auth import create_access_token, hash_password, verify_password
from .client_ip import client_ip
from .deps import get_current_user, get_session, get_settings
from .models import (
    AgentRun,
    AgentStep,
    AuditLog,
    Conversation,
    GeneratedImage,
    MemoryItem,
    Message,
    PlanLimit,
    Project,
    UsageRecord,
    User,
)
from .plans import get_limits, usage_today

router = APIRouter(tags=["account"])


@router.get("/v1/plan")
async def my_plan(user: User = Depends(get_current_user), session=Depends(get_session)):
    lim = await get_limits(session, user.plan or "free")
    used = await usage_today(session, user.id)
    return {
        "plan": lim.plan,
        "label": lim.label,
        "is_guest": bool(user.is_guest),
        "limits": {
            "messages": lim.daily_messages,
            "heavy": lim.daily_heavy,
            "images": lim.daily_images,
        },
        "used_today": {
            "messages": used["message"],
            "heavy": used["heavy"],
            "images": used["image"],
        },
        "resets": "00:00 UTC",
    }


@router.post("/v1/auth/guest")
async def guest_login(request: Request, session=Depends(get_session)):
    """Try the app without an email. Same quotas as Free, limited per IP."""
    if not request.app.state.register_limiter.allow(f"reg:{client_ip(request)}"):
        raise HTTPException(
            status_code=429, detail="محاولات كتير من نفس الجهاز — جرّب بعد ساعة"
        )
    gid = uuid.uuid4().hex
    user = User(
        email=f"guest-{gid}@guest.chatluck.local",
        password_hash=hash_password(uuid.uuid4().hex),
        display_name="ضيف",
        is_guest=True,
    )
    session.add(user)
    await session.commit()
    return {
        "access_token": create_access_token(user.id, get_settings(request)),
        "token_type": "bearer",
        "is_guest": True,
    }


@router.delete("/v1/auth/me", status_code=204)
async def delete_account(
    body: dict,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """Permanently delete the account and everything tied to it. Real accounts
    must confirm with their password; guests have none."""
    if not user.is_guest and not verify_password(body.get("password") or "", user.password_hash):
        raise HTTPException(status_code=401, detail="كلمة السر غير صحيحة")
    uid = user.id
    conv_ids = select(Conversation.id).where(Conversation.user_id == uid)
    run_ids = select(AgentRun.id).where(AgentRun.user_id == uid)
    await session.execute(delete(Message).where(Message.conversation_id.in_(conv_ids)))
    await session.execute(delete(AgentStep).where(AgentStep.run_id.in_(run_ids)))
    for model in (AgentRun, GeneratedImage, MemoryItem, Project, Conversation):
        await session.execute(delete(model).where(model.user_id == uid))
    await session.execute(delete(UsageRecord).where(UsageRecord.user_id == uid))
    await session.execute(delete(AuditLog).where(AuditLog.user_id == uid))
    await session.execute(delete(User).where(User.id == uid))
    await session.commit()


def _admin(request: Request, user: User) -> None:
    if user.email.lower() not in _admin_emails(request.app.state.settings):
        raise HTTPException(status_code=403, detail="غير مصرح لك بالوصول إلى لوحة المشرف")


@router.get("/v1/admin/plans")
async def admin_plans(
    request: Request, user: User = Depends(get_current_user), session=Depends(get_session)
):
    _admin(request, user)
    rows = (await session.execute(select(PlanLimit).order_by(PlanLimit.plan))).scalars().all()
    return [
        {
            "plan": r.plan,
            "label": r.label,
            "daily_messages": r.daily_messages,
            "daily_heavy": r.daily_heavy,
            "daily_images": r.daily_images,
        }
        for r in rows
    ]


@router.put("/v1/admin/plans/{plan}")
async def admin_edit_plan(
    plan: str,
    body: dict,
    request: Request,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    _admin(request, user)
    row = await session.get(PlanLimit, plan)
    if row is None:
        raise HTTPException(status_code=404, detail="plan not found")
    for key in ("daily_messages", "daily_heavy", "daily_images"):
        if key in body:
            val = body[key]
            if not isinstance(val, int) or isinstance(val, bool) or not 0 <= val <= 100000:
                raise HTTPException(status_code=422, detail=f"{key} must be 0..100000")
            setattr(row, key, val)
    await session.commit()
    return {"plan": row.plan, "daily_messages": row.daily_messages,
            "daily_heavy": row.daily_heavy, "daily_images": row.daily_images}


@router.put("/v1/admin/users/{email}/plan")
async def admin_set_user_plan(
    email: str,
    body: dict,
    request: Request,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    _admin(request, user)
    if await session.get(PlanLimit, body.get("plan") or "") is None:
        raise HTTPException(status_code=422, detail="unknown plan")
    target = (
        await session.execute(select(User).where(User.email == email.lower()))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    target.plan = body["plan"]
    await session.commit()
    return {"email": target.email, "plan": target.plan}


PRIVACY_HTML = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>سياسة الخصوصية - شات لاك</title>
<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:2rem auto;padding:0 1rem;line-height:1.8}</style></head><body>
<h1>سياسة الخصوصية</h1>
<p>شات لاك بيحفظ بس اللي محتاجه عشان التطبيق يشتغل: بريدك الإلكتروني واسمك، ومحادثاتك، وذاكرتك لو فعّلتها، والصور اللي أنشأتها.</p>
<h2>مين بيشوف بياناتك</h2>
<p>رسائلك بتتبعت لمزودي الذكاء الاصطناعي المجانيين (مثل Google Gemini) عشان يردوا عليك. بنحفظ سجل تقني بسيط (تسجيل الدخول والاستخدام اليومي) لمنع الإساءة.</p>
<h2>التحكم في بياناتك</h2>
<p>تقدر تمسح أي محادثة، وتصدّر ذاكرتك أو تطفيها، وتمسح حسابك بالكامل من الإعدادات. المسح نهائي ومش بنحتفظ بنسخة.</p>
<h2>وضع الضيف</h2>
<p>الضيف بيشتغل من غير بريد، وبياناته بتتمسح لما يمسح حسابه.</p>
<h2>الأطفال</h2><p>التطبيق مش موجه لأقل من 13 سنة.</p>
</body></html>"""


@router.get("/privacy", response_class=HTMLResponse)
async def privacy():
    return PRIVACY_HTML
