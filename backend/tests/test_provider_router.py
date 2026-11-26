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
