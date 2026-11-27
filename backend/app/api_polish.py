"""P2: custom assistants and read-only conversation sharing."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from .deps import get_current_user, get_session
from .models import Assistant, Conversation, Message, User

router = APIRouter(tags=["polish"])

MAX_ASSISTANTS = 20
MAX_INSTRUCTIONS = 4000


def _out(a: Assistant) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "instructions": a.instructions,
        "created_at": a.created_at.isoformat(),
    }


def _validate(body: dict, partial: bool = False) -> dict:
    out = {}
    if "name" in body or not partial:
        name = (body.get("name") or "").strip()
        if not name or len(name) > 60:
            raise HTTPException(status_code=422, detail="الاسم من حرف لـ 60 حرف")
        out["name"] = name
    if "instructions" in body or not partial:
        ins = body.get("instructions") or ""
        if not isinstance(ins, str) or len(ins) > MAX_INSTRUCTIONS:
            raise HTTPException(
                status_code=422, detail=f"التعليمات أقصاها {MAX_INSTRUCTIONS} حرف"
            )
        out["instructions"] = ins.strip()
    return out


async def _own(session, user: User, aid: str) -> Assistant:
    a = await session.get(Assistant, aid)
    if a is None or a.user_id != user.id:
        raise HTTPException(status_code=404, detail="assistant not found")
    return a


@router.get("/v1/assistants")
async def list_assistants(user: User = Depends(get_current_user), session=Depends(get_session)):
    rows = (
        await session.execute(
            select(Assistant).where(Assistant.user_id == user.id).order_by(Assistant.created_at)
        )
    ).scalars().all()
    return [_out(a) for a in rows]


@router.post("/v1/assistants")
async def create_assistant(
    body: dict, user: User = Depends(get_current_user), session=Depends(get_session)
):
    vals = _validate(body)
    count = (
        await session.execute(
            select(func.count()).select_from(Assistant).where(Assistant.user_id == user.id)
        )
    ).scalar_one()
    if count >= MAX_ASSISTANTS:
        raise HTTPException(status_code=409, detail=f"الحد الأقصى {MAX_ASSISTANTS} مساعد")
    a = Assistant(user_id=user.id, **vals)
    session.add(a)
    await session.commit()
    return _out(a)


@router.put("/v1/assistants/{aid}")
async def update_assistant(
    aid: str, body: dict, user: User = Depends(get_current_user), session=Depends(get_session)
):
    a = await _own(session, user, aid)
    for k, v in _validate(body, partial=True).items():
        setattr(a, k, v)
    await session.commit()
    return _out(a)


@router.delete("/v1/assistants/{aid}", status_code=204)
async def delete_assistant(
    aid: str, user: User = Depends(get_current_user), session=Depends(get_session)
):
    a = await _own(session, user, aid)
    rows = (
        await session.execute(select(Conversation).where(Conversation.assistant_id == aid))
    ).scalars().all()
    for conv in rows:  # keep the chats, just unbind
        conv.assistant_id = None
    await session.delete(a)
    await session.commit()


async def _own_conv(session, user: User, cid: str) -> Conversation:
    conv = await session.get(Conversation, cid)
    if conv is None or conv.user_id != user.id:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conv


@router.post("/v1/conversations/{cid}/share")
async def share_conversation(
    cid: str, user: User = Depends(get_current_user), session=Depends(get_session)
):
    """Create (or return) a read-only link. Anyone with the link can read it."""
    conv = await _own_conv(session, user, cid)
    if not conv.share_token:
        conv.share_token = secrets.token_urlsafe(24)
        await session.commit()
    return {"token": conv.share_token, "path": f"/v1/shared/{conv.share_token}"}


@router.delete("/v1/conversations/{cid}/share", status_code=204)
async def unshare_conversation(
    cid: str, user: User = Depends(get_current_user), session=Depends(get_session)
):
    conv = await _own_conv(session, user, cid)
    conv.share_token = None
    await session.commit()


@router.get("/v1/shared/{token}")
async def read_shared(token: str, session=Depends(get_session)):
    """Public, read-only. Returns title and messages only: no user id, email, or model keys."""
    if len(token) < 16:
        raise HTTPException(status_code=404, detail="not found")
    conv = (
        await session.execute(select(Conversation).where(Conversation.share_token == token))
    ).scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=404, detail="not found")
    msgs = (
        await session.execute(
            select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at)
        )
    ).scalars().all()
    return {
        "title": conv.title,
        "messages": [
            {"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
            for m in msgs
            if m.role in ("user", "assistant")
        ],
    }
