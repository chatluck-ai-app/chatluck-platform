import pytest

from app.providers.base import CHAT, STREAM, ProviderError
from app.providers.router import AllProvidersDownError
from conftest import FakeProvider, make_router

pytestmark = pytest.mark.asyncio


async def test_routes_to_configured_provider():
    unconfigured = FakeProvider(name="off", configured=False)
    on = FakeProvider(name="on")
    router = make_router(unconfigured, on)
    text, provider, model = await router.complete([{"role": "user", "content": "hi"}])
    assert provider == "on"
    assert model == "fake-1"
    assert text == on.reply


async def test_fallback_on_failure():
    bad = FakeProvider(name="bad", fail_times=10)
    good = FakeProvider(name="good", reply="نجاح")
    router = make_router(bad, good)
    text, provider, _ = await router.complete([{"role": "user", "content": "hi"}])
    assert (provider, text) == ("good", "نجاح")


async def test_circuit_breaker_skips_open_provider():
    clock = [0.0]
    bad = FakeProvider(name="bad", fail_times=100)
    good = FakeProvider(name="good")
    router = make_router(bad, good, failure_threshold=2)
    router.clock = lambda: clock[0]

    await router.complete([{"role": "user", "content": "hi"}])
    await router.complete([{"role": "user", "content": "hi"}])
    assert router._is_open("bad")

    calls_before = bad.calls
    await router.complete([{"role": "user", "content": "hi"}])
    assert bad.calls == calls_before  # skipped while circuit open

    clock[0] += 121  # cooldown elapsed
    await router.complete([{"role": "user", "content": "hi"}])
    assert bad.calls == calls_before + 3  # retried after cooldown (1 try + 2 transient retries)


async def test_manual_model_selection_is_honored():
    a = FakeProvider(name="a", model_id="model-a")
    b = FakeProvider(name="b", model_id="model-b")
    router = make_router(a, b)
    _, provider, model = await router.complete(
        [{"role": "user", "content": "hi"}], model="model-b"
    )
    assert (provider, model) == ("b", "model-b")


async def test_manual_selection_failure_does_not_silently_switch():
    a = FakeProvider(name="a", model_id="model-a", fail_times=10)
    b = FakeProvider(name="b", model_id="model-b")
    router = make_router(a, b)
    with pytest.raises(AllProvidersDownError):
        await router.complete([{"role": "user", "content": "hi"}], model="model-a")


async def test_all_providers_down_reports_attempts():
    bad1 = FakeProvider(name="bad1", fail_times=10)
    bad2 = FakeProvider(name="bad2", fail_times=10)
    router = make_router(bad1, bad2)
    with pytest.raises(AllProvidersDownError) as excinfo:
        await router.complete([{"role": "user", "content": "hi"}])
    assert len(excinfo.value.attempts) == 2


async def test_capability_filtering():
    chatty = FakeProvider(name="chatty", capabilities=(CHAT,))
    streamy = FakeProvider(name="streamy", capabilities=(STREAM,))
    router = make_router(chatty, streamy)
    _, provider, _ = await router.complete(
        [{"role": "user", "content": "hi"}], capability="chat"
    )
    assert provider == "chatty"


async def test_stream_fallback_before_first_token():
    bad = FakeProvider(name="bad", fail_times=10)
    good = FakeProvider(name="good")
    router = make_router(bad, good)
    events = [e async for e in router.stream([{"role": "user", "content": "hi"}])]
    types = [e["type"] for e in events]
    assert "fallback" in types
    assert types[-1] == "done"
    text = "".join(e.get("content", "") for e in events if e["type"] == "token")
    assert "شات لاك" in text


async def test_list_models_reports_availability():
    off = FakeProvider(name="off", configured=False)
    on = FakeProvider(name="on")
    router = make_router(off, on)
    models = router.list_models()
    by_provider = {m["provider"]: m["available"] for m in models}
    assert by_provider == {"off": False, "on": True}


async def test_retryable_failure_is_retried_before_fallback():
    from conftest import FakeProvider, make_router

    flaky = FakeProvider(name="flaky", fail_times=1)
    router = make_router(flaky)
    text, provider, _ = await router.complete([{"role": "user", "content": "hi"}])
    assert provider == "flaky"
    assert text == flaky.reply
    assert flaky.calls == 2  # one failure + one successful retry


class HangingProvider(FakeProvider):
    """Stalls without yielding. hang_times controls how many calls hang."""

    def __init__(self, *a, hang_times=999, hang=60.0, **kw):
        super().__init__(*a, **kw)
        self.hang_times = hang_times
        self.hang = hang

    async def chat_stream(self, messages, model=None, **kw):
        self.calls += 1
        if self.calls <= self.hang_times:
            import asyncio as _a

            await _a.sleep(self.hang)
        for token in ["ok"]:
            yield token


class StallMidProvider(FakeProvider):
    """Yields one token, then stalls forever."""

    async def chat_stream(self, messages, model=None, **kw):
        self.calls += 1
        yield "partial"
        import asyncio as _a

        await _a.sleep(60)


@pytest.fixture()
def fast_timeouts(monkeypatch):
    import app.providers.router as r

    monkeypatch.setattr(r, "_FIRST_TOKEN_TIMEOUT", 0.05)
    monkeypatch.setattr(r, "_NEXT_TOKEN_TIMEOUT", 0.05)


async def test_stall_before_first_token_retries_and_recovers(fast_timeouts):
    p = HangingProvider(name="hang", hang_times=1)
    router = make_router(p)
    events = [e async for e in router.stream([{"role": "user", "content": "hi"}])]
    tokens = [e["content"] for e in events if e["type"] == "token"]
    assert tokens == ["ok"]
    assert p.calls == 2  # hung once, retried, produced


async def test_permanent_stall_falls_back(fast_timeouts):
    bad = HangingProvider(name="hang")
    good = FakeProvider(name="good")
    router = make_router(bad, good)
    events = [e async for e in router.stream([{"role": "user", "content": "hi"}])]
    kinds = [e["type"] for e in events]
    assert "fallback" in kinds
    assert "token" in kinds
    assert kinds[-1] == "done"


async def test_midstream_stall_keeps_partial_reply(fast_timeouts):
    p = StallMidProvider(name="mid")
    router = make_router(p)
    events = [e async for e in router.stream([{"role": "user", "content": "hi"}])]
    tokens = [e["content"] for e in events if e["type"] == "token"]
    assert tokens == ["partial"]
    assert events[-1]["type"] == "done"
    assert p.calls == 1  # no retry after tokens were produced
