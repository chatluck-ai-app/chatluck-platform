import asyncio
import time

import pytest

import app.execution.search as search
from app.execution.search import SearchResult, search_web

pytestmark = pytest.mark.asyncio


def wiki_hits():
    return [
        SearchResult(
            title="سعر الدولار",
            url="https://ar.wikipedia.org/wiki/x",
            snippet="مقتطف",
            source="wikipedia",
        )
    ]


async def _hang(*a, **kw):
    await asyncio.sleep(30)
    return []


async def test_hanging_engines_never_starve_wikipedia(monkeypatch):
    monkeypatch.setattr(search, "_duckduckgo_html", _hang)
    monkeypatch.setattr(search, "_duckduckgo_lite", _hang)
    monkeypatch.setattr(search, "_mojeek", _hang)

    async def fake_wiki(query, client, limit, lang="ar"):
        return wiki_hits() if lang == "ar" else []

    monkeypatch.setattr(search, "_wikipedia", fake_wiki)
    t0 = time.monotonic()
    results = await search_web("سعر الدولار")
    elapsed = time.monotonic() - t0
    assert results and results[0].source == "wikipedia"
    assert elapsed < 5.0  # would be 90s+ if the hangs were sequential


async def test_everything_hanging_returns_empty_fast(monkeypatch):
    monkeypatch.setattr(search, "_duckduckgo_html", _hang)
    monkeypatch.setattr(search, "_duckduckgo_lite", _hang)
    monkeypatch.setattr(search, "_mojeek", _hang)
    monkeypatch.setattr(search, "_wikipedia", _hang)
    t0 = time.monotonic()
    # _wikipedia is called inside _wiki_chain with the real (2.5s) client but
    # our hang ignores it; bound the whole call the way _grounding does.
    results = await asyncio.wait_for(search_web("أخبار"), timeout=6.0)
    elapsed = time.monotonic() - t0
    assert results == []
    assert elapsed < 6.0


async def test_engine_results_preferred_and_wiki_appended(monkeypatch):
    async def ddg(query, client, limit):
        return [
            SearchResult(title="t", url="https://ddg.example/x", snippet="s", source="duckduckgo")
        ]

    async def fail(*a, **kw):
        raise RuntimeError("blocked")

    async def fake_wiki(query, client, limit, lang="ar"):
        return wiki_hits() if lang == "ar" else []

    monkeypatch.setattr(search, "_duckduckgo_html", ddg)
    monkeypatch.setattr(search, "_duckduckgo_lite", fail)
    monkeypatch.setattr(search, "_mojeek", fail)
    monkeypatch.setattr(search, "_wikipedia", fake_wiki)
    results = await search_web("query")
    assert results[0].source == "duckduckgo"
    assert any(r.source == "wikipedia" for r in results)
