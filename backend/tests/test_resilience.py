"""Nexus/Chat Luck saturation fixes: per-model breaker, 429 fallthrough,
research degrade, per-device heavy limit."""
import pytest

from app.execution.search import SearchResult
from app.providers.base import CHAT, STREAM, VISION, BaseProvider, ModelInfo, ProviderError
from conftest import FakeProvider, auth_headers, make_router, register_user

pytestmark = pytest.mark.asyncio


class MultiModelProvider(BaseProvider):
    """One provider, several models, per-model scripted status codes."""

    name = "multi"

    def __init__(self, behaviors):
        self.behaviors = behaviors  # model id -> HTTP status to raise, or None = ok
        self.calls = []

    def models(self):
        return [
            ModelInfo(id=i, provider=self.name, capabilities=frozenset({CHAT, STREAM, VISION}))
            for i in self.behaviors
        ]

    def is_configured(self):
        return True

    async def chat(self, messages, model=None, **kw):
        self.calls.append(model)
        st = self.behaviors[model]
        if st:
            raise ProviderError(f"HTTP {st}", self.name, retryable=st in (429, 503), status=st)
        return f"ok from {model}"

    async def chat_stream(self, messages, model=None, **kw):
        yield await self.chat(messages, model)


MSG = [{"role": "user", "content": "hi"}]


async def test_429_goes_straight_to_next_model_without_retrying():
    p = MultiModelProvider({"m1": 429, "m2": None})
    router = make_router(p)
    text, prov, model = await router.complete(MSG, capability="vision")
    assert (prov, model) == ("multi", "m2")
    assert p.calls == ["m1", "m2"]  # m1 tried exactly once


async def test_saturated_model_is_skipped_but_siblings_stay_online():
    p = MultiModelProvider({"m1": 429, "m2": None})
    router = make_router(p)
    await router.complete(MSG)
    p.calls.clear()
    await router.complete(MSG)
    assert p.calls == ["m2"]  # m1 in short quota cooldown, provider NOT taken offline
    assert not router._is_open("multi")
    avail = {m["id"]: m["available"] for m in router.list_models()}
    assert avail == {"m1": False, "m2": True}


async def test_quota_cooldown_expires():
    clock = [0.0]
    p = MultiModelProvider({"m1": 429, "m2": None})
    router = make_router(p)
    router.clock = lambda: clock[0]
    await router.complete(MSG)
    clock[0] += 31
    p.calls.clear()
    await router.complete(MSG)
    assert p.calls[0] == "m1"  # retried after the 30s quota cooldown


async def test_503_is_retried_on_same_model_then_falls_back(monkeypatch):
    async def nosleep(_):
        return None
    monkeypatch.setattr("app.providers.router.asyncio.sleep", nosleep)
    p = MultiModelProvider({"m1": 503, "m2": None})
    router = make_router(p)
    _, _, model = await router.complete(MSG)
    assert model == "m2"
    assert p.calls.count("m1") == 3  # 1 try + 2 backoff retries


async def test_research_degrades_to_sources_when_models_are_down(client, monkeypatch):
    async def fake_search(q, limit=5, **kw):
        return [SearchResult(title="مصدر", url="https://example.com/a", snippet="نص", source="wikipedia")]
    monkeypatch.setattr("app.api_research.search_web", fake_search)
    client.app.state.provider_router = make_router(FakeProvider(name="down", fail_times=100))
    token = await register_user(client)
    r = await client.post(
        "/v1/research", json={"query": "اختبار البحث", "language": "ar"}, headers=auth_headers(token)
    )
    assert r.status_code == 200
    j = r.json()
    assert j["provider"] == "none"
    assert "https://example.com/a" in j["answer"]
    assert j["sources"][0]["url"] == "https://example.com/a"
    assert "scripted failure" not in j["answer"]  # no raw provider text leaked


async def test_research_is_rate_limited_per_device(client, monkeypatch):
    async def fake_search(q, limit=5, **kw):
        return [SearchResult(title="t", url="https://example.com", snippet="s", source="wikipedia")]
    monkeypatch.setattr("app.api_research.search_web", fake_search)
    token = await register_user(client)
    codes = []
    for _ in range(9):
        r = await client.post(
            "/v1/research", json={"query": "اختبار البحث", "language": "ar"}, headers=auth_headers(token)
        )
        codes.append(r.status_code)
    assert codes[:6] == [200] * 6
    assert codes[6:] == [429] * 3
    # a different device is not affected
    t2 = await register_user(client, email="other@example.com")
    r = await client.post(
        "/v1/research", json={"query": "اختبار البحث", "language": "ar"}, headers=auth_headers(t2)
    )
    assert r.status_code == 200
