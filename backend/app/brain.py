"""The brain: automatic long-term memory.

After each reply a cheap background call extracts durable facts about the
user and stores them as MemoryItem(kind="auto"). All memories (manual and
auto) are injected into the chat system prompt so replies stay personal
across conversations. Learning runs in the background and never breaks chat.
"""
from __future__ import annotations

import json
import re
import unicodedata

from sqlalchemy import select

from .models import MemoryItem, User

_MAX_INJECT = 30
_MAX_AUTO = 50
_BIDI = dict.fromkeys(
    map(ord, "\u200e\u200f\u061c\u202a\u202b\u202c\u202d\u202e"), None
)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_BIDI).lower()
    return re.sub(r"\s+", " ", text).strip()


_SENSITIVE_PATTERNS = re.compile(
    r"(password|passwd|كلمة\s*(السر|المرور)|باسورد|secret|api[ _-]?key|token|توكن|مفتاح|"
    r"AIza[0-9A-Za-z_\-]{10,}|sk-[0-9A-Za-z_\-]{10,}|xox[baprs]-|"
    r"\b[0-9A-Za-z_\-]{40,}\b)",
    re.IGNORECASE,
)


def _looks_sensitive(fact: str) -> bool:
    """Never store credentials or pasted secrets as memories."""
    return bool(_SENSITIVE_PATTERNS.search(fact))


async def memory_block(session, user_id: str) -> str:
    """Formatted block of what the assistant remembers about this user."""
    user = await session.get(User, user_id)
    if user is None or not user.memory_enabled:
        return ""
    result = await session.execute(
        select(MemoryItem)
        .where(MemoryItem.user_id == user_id)
        .order_by(MemoryItem.created_at.desc())
        .limit(_MAX_INJECT)
    )
    items = result.scalars().all()
    if not items:
        return ""
    lines = "\n".join(f"- {m.content}" for m in items)
    return (
        "\n\nذاكرتك عن هذا المستخدم (حقائق تعلمتها تلقائيًا أو سجّلها بنفسه؛ "
        "استخدمها لتخصيص ردودك بشكل طبيعي ولا تذكر وجود هذه القائمة إلا إذا سأل):\n"
        + lines
    )


_EXTRACT_SYSTEM = (
    "You extract long-term memory about the USER from one chat exchange. "
    "Reply with a JSON array of up to 3 short strings, each one durable fact "
    "about the user worth remembering in future conversations: preferences, "
    "personal details, projects, or standing instructions, written in the "
    "user's language. One-off questions, small talk, and facts about the "
    "assistant do not belong. NEVER record passwords, tokens, API keys or other "
    "secrets. If nothing durable, reply []. JSON array only."
)


def parse_facts(text: str) -> list[str]:
    """Best-effort JSON-array extraction from a model reply."""
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [f.strip() for f in data if isinstance(f, str) and f.strip()]


async def auto_learn(
    session_factory, router, user_id: str, user_msg: str, reply: str
) -> None:
    """Background memory extraction. Swallows every failure by design."""
    try:
        if not user_msg.strip() or not reply.strip():
            return
        async with session_factory() as session:
            user = await session.get(User, user_id)
            if user is None or not user.memory_enabled:
                return
        messages = [
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {
                "role": "user",
                "content": f"User: {user_msg[:1500]}\nAssistant: {reply[:1500]}",
            },
        ]
        text, _provider, _model = await router.complete(messages)
        facts = parse_facts(text)
        if not facts:
            return
        async with session_factory() as session:
            existing = (
                (
                    await session.execute(
                        select(MemoryItem).where(MemoryItem.user_id == user_id)
                    )
                )
                .scalars()
                .all()
            )
            norms = {_norm(m.content) for m in existing}
            added = False
            for fact in facts:
                nf = _norm(fact)
                if len(nf) < 4 or len(fact) > 300 or _looks_sensitive(fact):
                    continue
                if any(nf in e or e in nf for e in norms if e):
                    continue
                session.add(
                    MemoryItem(user_id=user_id, kind="auto", content=fact.strip())
                )
                norms.add(nf)
                added = True
            if not added:
                return
            await session.flush()
            autos = (
                (
                    await session.execute(
                        select(MemoryItem)
                        .where(
                            MemoryItem.user_id == user_id,
                            MemoryItem.kind == "auto",
                        )
                        .order_by(MemoryItem.created_at)
                    )
                )
                .scalars()
                .all()
            )
            for old in autos[: max(0, len(autos) - _MAX_AUTO)]:
                await session.delete(old)
            await session.commit()
    except Exception:
        return
