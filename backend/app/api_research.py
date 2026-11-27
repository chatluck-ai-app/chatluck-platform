"""Research endpoint: live web search + LLM synthesis with cited sources."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .api_chat import _check_rate_limit, friendly_error
from .deps import audit, get_current_user, get_router, get_session
from .execution.search import search_web
from .models import User

router = APIRouter(prefix="/v1", tags=["research"])


class ResearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    language: str = Field(default="ar")


class SourceOut(BaseModel):
    title: str
    url: str
    snippet: str
    source: str


class ResearchResponse(BaseModel):
    answer: str
    provider: str
    model: str
    sources: list[SourceOut]


@router.post("/research", response_model=ResearchResponse)
async def research(
    body: ResearchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    _check_rate_limit(request, user, heavy=True)
    results = await search_web(body.query, limit=5)
    if not results:
        raise HTTPException(
            status_code=503,
            detail="مفيش نتائج بحث متاحة دلوقتي، جرب تاني بعد شوية.",
            headers={"Retry-After": "10"},
        )
    numbered = "\n".join(
        f"[{i + 1}] {r.title} — {r.url}\n{r.snippet}" for i, r in enumerate(results)
    )
    lang = "Arabic" if body.language == "ar" else body.language
    messages = [
        {
            "role": "system",
            "content": (
                f"You are the Chat Luck AI research agent. Answer in {lang}. "
                "Use ONLY the numbered sources below. Cite every claim as [n] "
                "matching its source number. If sources are insufficient, say so "
                "honestly instead of inventing.\n\nSources:\n" + numbered
            ),
        },
        {"role": "user", "content": body.query},
    ]
    router = get_router(request)
    try:
        answer, provider_name, model_id = await router.complete(messages)
    except Exception as exc:
        # The model layer is saturated or down, but the search itself worked:
        # return the real sources with an honest note instead of an error.
        answer = (
            friendly_error(str(exc))
            + "\n\nدي المصادر اللي لقيتها (من غير تلخيص):\n"
            + "\n".join(f"[{i + 1}] {r.title} — {r.url}" for i, r in enumerate(results))
        )
        provider_name, model_id = "none", "none"
    await audit(session, user.id, "research.query", body.query[:120])
    await session.commit()
    return ResearchResponse(
        answer=answer,
        provider=provider_name,
        model=model_id,
        sources=[
            SourceOut(title=r.title, url=r.url, snippet=r.snippet, source=r.source)
            for r in results
        ],
    )
