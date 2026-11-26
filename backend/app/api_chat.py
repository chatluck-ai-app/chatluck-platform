"""Chat endpoints: conversations, messages, streaming and non-streaming chat,
model catalog, image generation, and user-controlled memory."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from .deps import (
    audit,
    get_current_user,
    get_rate_limiter,
    get_router,
    get_session,
)
from .models import Conversation, GeneratedImage, MemoryItem, Message, User
from .providers.base import Message as ProviderMessage
from .schemas import (
    ChatRequest,
    ChatResponse,
    ConversationResponse,
    MemoryItemRequest,
    MemoryItemResponse,
    ModelInfoResponse,
)

router = APIRouter(prefix="/v1", tags=["chat"])

SYSTEM_PROMPT = (
    "You are Chat Luck AI, a sharp, warm multi-model assistant. "
    "Answer in the user's language; if they write Arabic, answer in clear "
    "Modern Standard Arabic, and Egyptian colloquial is welcome when they "
    "use it. Give complete, useful answers, never one-liners, unless the "
    "user explicitly asks for brevity. Structure longer answers with "
    "Markdown: short intro, ## headings, bullet lists, **bold** for key "
    "terms, numbered steps for how-tos, and fenced code blocks with the "
    "language name for code. Explain the reasoning briefly when it helps. "
    "Give concrete examples. If you are unsure, say so honestly instead of "
    "inventing facts. End with a short follow-up suggestion only when it "
    "genuinely helps the user continue."
)


def friendly_error(raw: str) -> str:
    """Map raw provider/infra errors to a friendly Arabic user message."""
    low = raw.lower()
    if any(k in low for k in ("503", "high demand", "unavailable", "overload")):
        return "الخدمة مشغولة شوية دلوقتي بسبب الضغط، جرب تاني بعد ثواني."
    if any(k in low for k in ("429", "rate limit", "quota", "too many")):
        return "وصلنا للحد المؤقت للطلبات، استنى دقيقة وجرب تاني."
    if any(k in low for k in ("no provider", "all providers failed")):
        return "مفيش مزود ذكاء اصطناعي متاح حالياً، جرب بعد شوية."
    if any(k in low for k in ("timeout", "timed out")):
        return "المزود استغرق وقت أطول من اللازم، جرب تاني."
    return "حصل خطأ غير متوقع أثناء تنفيذ طلبك، جرب تاني."


async def _load_conv_messages(session, conversation_id: str) -> list[Message]:
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
    )
    return list(result.scalars().all())


async def _apply_edit_or_regenerate(session, conv: Conversation, body: ChatRequest) -> None:
    """For regenerate: drop trailing assistant messages after the last user
    message. For edit: update the target user message and drop everything
    after it. Raises 404/422 on bad targets."""
    messages = await _load_conv_messages(session, conv.id)
    if body.edit_message_id:
        idx = next(
            (i for i, m in enumerate(messages) if m.id == body.edit_message_id),
            None,
        )
        if idx is None or messages[idx].role != "user":
            raise HTTPException(status_code=404, detail="message not found")
        messages[idx].content = body.message
        for m in messages[idx + 1 :]:
            await session.delete(m)
        await session.flush()
        return
    # regenerate
    idx = len(messages) - 1
    while idx >= 0 and messages[idx].role != "user":
        idx -= 1
    if idx < 0:
        raise HTTPException(
            status_code=422, detail="no user message to regenerate from"
        )
    for m in messages[idx + 1 :]:
        await session.delete(m)
    await session.flush()


def _check_rate_limit(request: Request, user: User) -> None:
    limiter = get_rate_limiter(request)
    if not limiter.allow(user.id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded, slow down",
        )


async def _load_history(session, conversation_id: str) -> list[ProviderMessage]:
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    return [{"role": m.role, "content": m.content} for m in result.scalars().all()]


async def _get_or_create_conversation(
    session, user: User, conversation_id: str | None, first_message: str
) -> Conversation:
    if conversation_id:
        result = await session.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conv = result.scalar_one_or_none()
        if conv is None or conv.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found"
            )
        return conv
    conv = Conversation(user_id=user.id, title=first_message[:80])
    session.add(conv)
    await session.flush()
    return conv


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    _check_rate_limit(request, user)
    if body.stream:
        return await _chat_stream(body, request, user, session)
    conv = await _get_or_create_conversation(
        session, user, body.conversation_id, body.title_hint or body.message
    )
    if body.regenerate or body.edit_message_id:
        await _apply_edit_or_regenerate(session, conv, body)
    else:
        stored = body.message
        if body.attachments:
            kinds = ", ".join(a.mime_type for a in body.attachments)
            stored = f"{body.message}\n[مرفقات: {kinds}]"
        session.add(Message(conversation_id=conv.id, role="user", content=stored))
        await session.flush()

    history = [{"role": "system", "content": SYSTEM_PROMPT}]
    history += await _load_history(session, conv.id)
    if body.attachments:
        history[-1] = {
            "role": "user",
            "content": body.message,
            "attachments": [a.model_dump() for a in body.attachments],
        }

    router = get_router(request)
    capability = "vision" if body.attachments else "chat"
    try:
        text, provider_name, model_id = await router.complete(
            history, capability=capability, model=body.model
        )
    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=friendly_error(str(exc)),
        ) from exc

    reply = Message(
        conversation_id=conv.id,
        role="assistant",
        content=text,
        model=model_id,
        provider=provider_name,
    )
    session.add(reply)
    await session.flush()
    await audit(session, user.id, "chat.complete", f"provider={provider_name}")
    await session.commit()
    return ChatResponse(
        conversation_id=conv.id,
        message_id=reply.id,
        content=text,
        model=model_id,
        provider=provider_name,
    )


async def _chat_stream(body: ChatRequest, request: Request, user: User, session):
    conv = await _get_or_create_conversation(
        session, user, body.conversation_id, body.title_hint or body.message
    )
    if body.regenerate or body.edit_message_id:
        await _apply_edit_or_regenerate(session, conv, body)
    else:
        session.add(
            Message(conversation_id=conv.id, role="user", content=body.message)
        )
    # Commit now: the generator writes the assistant reply in a separate
    # session after the response starts, and SQLite allows one writer.
    await session.commit()
    history = [{"role": "system", "content": SYSTEM_PROMPT}]
    history += await _load_history(session, conv.id)
    conversation_id = conv.id
    provider_router = get_router(request)
    session_factory = request.app.state.session_factory
    user_id = user.id

    async def event_source():
        collected: list[str] = []
        provider_name = model_id = None
        errored = False
        yield f"event: meta\ndata: {json.dumps({'conversation_id': conversation_id})}\n\n"
        async for event in provider_router.stream(history, model=body.model):
            if event["type"] == "meta":
                provider_name, model_id = event["provider"], event["model"]
            elif event["type"] == "token":
                collected.append(event["content"])
                yield f"event: token\ndata: {json.dumps({'content': event['content']})}\n\n"
            elif event["type"] == "fallback":
                yield f"event: fallback\ndata: {json.dumps({'from': event['from'], 'reason': event['reason']})}\n\n"
            elif event["type"] == "error":
                errored = True
                yield f"event: error\ndata: {json.dumps({'error': friendly_error(event['error'])})}\n\n"
        # Persist in a fresh session: the request session may have closed.
        if collected:
            async with session_factory() as write_session:
                write_session.add(
                    Message(
                        conversation_id=conversation_id,
                        role="assistant",
                        content="".join(collected),
                        model=model_id,
                        provider=provider_name,
                    )
                )
                await audit(write_session, user_id, "chat.stream", f"provider={provider_name}")
                await write_session.commit()
        if not errored:
            yield f"event: done\ndata: {json.dumps({'provider': provider_name, 'model': model_id})}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")


@router.get("/models", response_model=list[ModelInfoResponse])
async def list_models(request: Request, user: User = Depends(get_current_user)):
    router = get_router(request)
    return [ModelInfoResponse(**m) for m in router.list_models()]


@router.get("/conversations/search")
async def search_conversations(
    q: str,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    """Search the user's conversations by title and message content.
    Returns matching conversations with a snippet of the first match."""
    query = q.strip()
    if not query:
        return []
    like = f"%{query}%"
    result = await session.execute(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
    )
    convs = list(result.scalars().all())
    matches = []
    for conv in convs:
        snippet = None
        if query.lower() in conv.title.lower():
            snippet = conv.title
        else:
            msgs = await _load_conv_messages(session, conv.id)
            for m in msgs:
                pos = m.content.lower().find(query.lower())
                if pos >= 0:
                    start = max(0, pos - 40)
                    snippet = ("…" if start else "") + m.content[start : pos + 80]
                    break
        if snippet is not None:
            matches.append(
                {
                    "id": conv.id,
                    "title": conv.title,
                    "updated_at": conv.updated_at.isoformat(),
                    "snippet": snippet,
                }
            )
    return matches


IMAGE_STYLES = {
    "realistic": "ultra realistic photograph, detailed, ",
    "anime": "anime style illustration, ",
    "oil": "oil painting, artistic, ",
    "3d": "3d render, octane, high detail, ",
    "fantasy": "fantasy art, magical, ",
}


@router.post("/images")
async def generate_image(
    body: dict,
    request: Request,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    _check_rate_limit(request, user)
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="prompt is required")
    width = int(body.get("width") or 1024)
    height = int(body.get("height") or 1024)
    if not (256 <= width <= 1920) or not (256 <= height <= 1920):
        raise HTTPException(status_code=422, detail="invalid dimensions")
    style = (body.get("style") or "").strip()
    styled_prompt = IMAGE_STYLES.get(style, "") + prompt
    import random

    router = get_router(request)
    try:
        url, provider_name, model_id = await router.generate_image(
            styled_prompt, width=width, height=height, seed=random.randint(1, 10**9)
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=friendly_error(str(exc)),
        ) from exc
    row = GeneratedImage(
        user_id=user.id,
        prompt=prompt,
        url=url,
        provider=provider_name,
        model=model_id,
        width=width,
        height=height,
        style=style,
    )
    session.add(row)
    await audit(session, user.id, "image.generate", f"provider={provider_name}")
    await session.commit()
    return {
        "id": row.id,
        "url": url,
        "provider": provider_name,
        "model": model_id,
        "prompt": prompt,
        "width": width,
        "height": height,
        "style": style,
    }


@router.get("/images/history")
async def image_history(
    user: User = Depends(get_current_user), session=Depends(get_session)
):
    result = await session.execute(
        select(GeneratedImage)
        .where(GeneratedImage.user_id == user.id)
        .order_by(GeneratedImage.created_at.desc())
    )
    return [
        {
            "id": g.id,
            "prompt": g.prompt,
            "url": g.url,
            "provider": g.provider,
            "model": g.model,
            "width": g.width,
            "height": g.height,
            "style": g.style,
            "created_at": g.created_at.isoformat(),
        }
        for g in result.scalars().all()
    ]


@router.get("/conversations", response_model=list[ConversationResponse])
async def list_conversations(
    user: User = Depends(get_current_user), session=Depends(get_session)
):
    result = await session.execute(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
    )
    return [
        ConversationResponse(
            id=c.id,
            title=c.title,
            created_at=c.created_at.isoformat(),
            updated_at=c.updated_at.isoformat(),
        )
        for c in result.scalars().all()
    ]


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    result = await session.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conv = result.scalar_one_or_none()
    if conv is None or conv.user_id != user.id:
        raise HTTPException(status_code=404, detail="conversation not found")
    msgs = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "model": m.model,
            "provider": m.provider,
            "created_at": m.created_at.isoformat(),
        }
        for m in msgs.scalars().all()
    ]


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    result = await session.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conv = result.scalar_one_or_none()
    if conv is None or conv.user_id != user.id:
        raise HTTPException(status_code=404, detail="conversation not found")
    await session.delete(conv)
    await session.commit()


@router.patch("/conversations/{conversation_id}", response_model=ConversationResponse)
async def rename_conversation(
    conversation_id: str,
    body: dict,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    result = await session.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conv = result.scalar_one_or_none()
    if conv is None or conv.user_id != user.id:
        raise HTTPException(status_code=404, detail="conversation not found")
    title = (body.get("title") or "").strip()
    if not title or len(title) > 200:
        raise HTTPException(status_code=422, detail="title must be 1-200 characters")
    conv.title = title
    await session.commit()
    return ConversationResponse(
        id=conv.id,
        title=conv.title,
        created_at=conv.created_at.isoformat(),
        updated_at=conv.updated_at.isoformat(),
    )


# ---- memory ------------------------------------------------------------


@router.get("/memory", response_model=list[MemoryItemResponse])
async def list_memory(user: User = Depends(get_current_user), session=Depends(get_session)):
    result = await session.execute(
        select(MemoryItem).where(MemoryItem.user_id == user.id)
    )
    return [
        MemoryItemResponse(
            id=m.id, kind=m.kind, content=m.content, created_at=m.created_at.isoformat()
        )
        for m in result.scalars().all()
    ]


@router.post("/memory", response_model=MemoryItemResponse)
async def add_memory(
    body: MemoryItemRequest,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    item = MemoryItem(user_id=user.id, kind=body.kind, content=body.content)
    session.add(item)
    await session.flush()
    await session.commit()
    return MemoryItemResponse(
        id=item.id, kind=item.kind, content=item.content,
        created_at=item.created_at.isoformat(),
    )


@router.delete("/memory/{item_id}", status_code=204)
async def delete_memory(
    item_id: str,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    result = await session.execute(
        select(MemoryItem).where(MemoryItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if item is None or item.user_id != user.id:
        raise HTTPException(status_code=404, detail="memory item not found")
    await session.delete(item)
    await audit(session, user.id, "memory.delete", item_id)
    await session.commit()


@router.get("/memory/export")
async def export_memory(user: User = Depends(get_current_user), session=Depends(get_session)):
    result = await session.execute(
        select(MemoryItem).where(MemoryItem.user_id == user.id)
    )
    return {
        "user_id": user.id,
        "items": [
            {"id": m.id, "kind": m.kind, "content": m.content,
             "created_at": m.created_at.isoformat()}
            for m in result.scalars().all()
        ],
    }
