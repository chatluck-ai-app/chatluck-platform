"""Chat endpoints: conversations, messages, streaming and non-streaming chat,
model catalog, image generation, and user-controlled memory."""
from __future__ import annotations

import asyncio
import json
import re

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
from .brain import auto_learn, memory_block
from .execution.search import search_web
from .models import Conversation, GeneratedImage, MemoryItem, Message, User
from .providers.base import Message as ProviderMessage
from .schemas import (
    ChatRequest,
    ChatResponse,
    ConversationResponse,
    MemoryItemRequest,
    MemoryItemResponse,
    MemoryItemUpdate,
    ModelInfoResponse,
    UserSettingsIn,
    UserSettingsOut,
)

router = APIRouter(prefix="/v1", tags=["chat"])

# Auto-grounding: questions about current facts (news, prices, weather,
# scores, schedules...) get a quick free web search injected into the prompt
# and the sources sent to the client. Heuristic-only, so ordinary chat
# messages pay zero extra latency.
_NEEDS_SEARCH = re.compile(
    r"(أخبار|خبر|النهارده|النهاردة|دلوقتي|سعر|أسعار|بكام|نتيجة|ماتش|مباراة|"
    r"طقس|درجة الحرارة|موعد|آخر|أحدث|عرض سينما|فيلم جديد|مسلسل جديد|"
    r"news|latest|price|cost|weather|today|tonight|now\b|score|match)",
    re.IGNORECASE,
)


async def _grounding(message: str) -> tuple[str, list[dict]]:
    """(prompt block, sources) when the message needs fresh facts, else ("", [])."""
    if not message or not _NEEDS_SEARCH.search(message):
        return "", []
    try:
        results = await asyncio.wait_for(search_web(message, limit=4), timeout=6.0)
    except Exception:
        return "", []
    if not results:
        return "", []
    lines = []
    sources = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r.title} — {r.url}\n{r.snippet}")
        sources.append(
            {"title": r.title, "url": r.url, "snippet": r.snippet, "source": r.source}
        )
    block = (
        "\n\nنتائج بحث حديثة من الويب لسؤال المستخدم. قواعد صارمة: "
        "(1) أي معلومة عن أخبار أو أسعار أو وقائع حالية يجب أن تأتي من هذه "
        "المصادر فقط مع استشهاد [رقم]. "
        "(2) لو المصادر مش بتغطي سؤاله أو مش مرتبطة بيه، قول بصراحة إن نتائج "
        "البحث مش كافية، ومتعرضش أحداث أو أخبار من عندك كأنها حالية أبدًا. "
        "(3) الأسئلة العامة اللي مش محتاجة وقت (زي التعريفات) جاوب عليها عادي "
        "من معرفتك من غير ما تنسبها للمصادر:\n" + "\n".join(lines)
    )
    return block, sources

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


def _check_rate_limit(request: Request, user: User, heavy: bool = False) -> None:
    limiter = get_rate_limiter(request)
    if heavy and not request.app.state.heavy_limiter.allow(user.id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="طلبات الصور والبحث كتيرة على جهازك، استنى شوية وجرب تاني.",
            headers={"Retry-After": "15"},
        )
    if not limiter.allow(user.id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded, slow down",
        )
    # Global guards so public traffic cannot drain the free provider keys.
    if not request.app.state.global_chat_limiter.allow("global"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="الخدمة مشغولة دلوقتي — جرّب بعد دقيقة",
        )
    if not request.app.state.daily_chat.allow():
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="وصلنا لحد الخدمة المجانية النهاردة — نرجع نكمل بكرة",
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
    _check_rate_limit(request, user, heavy=bool(body.attachments))
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

    grounding_block, sources = await _grounding(body.message)
    history = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT + await memory_block(session, user.id) + grounding_block,
        }
    ]
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
    asyncio.create_task(
        auto_learn(
            request.app.state.session_factory, router, user.id, body.message, text
        )
    )
    return ChatResponse(
        conversation_id=conv.id,
        message_id=reply.id,
        content=text,
        model=model_id,
        provider=provider_name,
        sources=sources or None,
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
    grounding_block, sources = await _grounding(body.message)
    history = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT + await memory_block(session, user.id) + grounding_block,
        }
    ]
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
        if sources:
            yield f"event: sources\ndata: {json.dumps({'sources': sources}, ensure_ascii=False)}\n\n"
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
            asyncio.create_task(
                auto_learn(
                    session_factory, provider_router, user_id, body.message, "".join(collected)
                )
            )
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


# --- image generation robustness -------------------------------------------
# Pollinations' anonymous tier sometimes answers HTTP 200 with a "busy"
# placeholder image (always 1024x576) instead of the requested picture. The
# client cannot detect that cross-origin, so the backend fetches each
# candidate URL, checks the real pixel dimensions, and retries with a fresh
# seed (Pollinations caches per URL, so the seed must change) until the
# returned image actually matches what the user asked for.

_IMG_RETRY_ATTEMPTS = 4
_IMG_RETRY_SLEEP = 15.0


def _image_dims(data: bytes) -> tuple[int, int] | None:
    """(width, height) from PNG or JPEG headers, stdlib only."""
    import struct

    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                return w, h
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg = struct.unpack(">H", data[i + 2 : i + 4])[0]
            i += 2 + seg
    return None


async def _fetch_image_dims(url: str) -> tuple[int, int] | None:
    try:
        import httpx

        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return None
        if not (resp.headers.get("content-type") or "").startswith("image/"):
            return None
        return _image_dims(resp.content)
    except Exception:
        return None


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
    import asyncio
    import random

    router = get_router(request)
    url = provider_name = model_id = None
    for attempt in range(_IMG_RETRY_ATTEMPTS):
        try:
            url, provider_name, model_id = await router.generate_image(
                styled_prompt, width=width, height=height, seed=random.randint(1, 10**9)
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=friendly_error(str(exc)),
            ) from exc
        if await _fetch_image_dims(url) == (width, height):
            break
        if attempt + 1 < _IMG_RETRY_ATTEMPTS:
            await asyncio.sleep(_IMG_RETRY_SLEEP)
    else:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="خدمة إنشاء الصور مشغولة دلوقتي — جرب تاني بعد دقيقة.",
        )
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


@router.put("/memory/{item_id}", response_model=MemoryItemResponse)
async def update_memory(
    item_id: str,
    body: MemoryItemUpdate,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    result = await session.execute(
        select(MemoryItem).where(MemoryItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if item is None or item.user_id != user.id:
        raise HTTPException(status_code=404, detail="memory item not found")
    item.content = body.content
    await audit(session, user.id, "memory.update", item_id)
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


@router.get("/settings", response_model=UserSettingsOut)
async def get_user_settings(user: User = Depends(get_current_user)):
    return UserSettingsOut(memory_enabled=user.memory_enabled)


@router.put("/settings", response_model=UserSettingsOut)
async def put_user_settings(
    body: UserSettingsIn,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    user.memory_enabled = body.memory_enabled
    await audit(
        session, user.id, "settings.update", f"memory_enabled={body.memory_enabled}"
    )
    await session.commit()
    return UserSettingsOut(memory_enabled=user.memory_enabled)


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
