import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.brain import _looks_sensitive, auto_learn, memory_block
from app.config import Settings
from app.db import init_db
from app.main import create_app
from app.models import MemoryItem, User
from app.rate_limit import RateLimiter
from conftest import FakeProvider, auth_headers, make_router, register_user

pytestmark = pytest.mark.asyncio


def low_limit_settings(tmp_path, **over):
    base = dict(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/g.db",
        jwt_secret="test-secret",
        rate_limit_per_minute=1000,
        register_per_hour_per_ip=100000,
        global_chat_per_minute=100000,
        global_chat_per_day=100000,
    )
    base.update(over)
    return Settings(**base)


async def make_client(settings):
    app = create_app(
        settings, provider_router=make_router(FakeProvider()), rate_limiter=RateLimiter(1000)
    )
    await init_db(app.state.engine)
    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    c.app = app
    return c


async def test_register_per_ip_limit(tmp_path):
    c = await make_client(low_limit_settings(tmp_path, register_per_hour_per_ip=2))
    for i in range(2):
        r = await c.post(
            "/v1/auth/register",
            json={"email": f"u{i}@example.com", "password": "secret123", "display_name": "u"},
        )
        assert r.status_code == 200, r.text
    r = await c.post(
        "/v1/auth/register",
        json={"email": "u3@example.com", "password": "secret123", "display_name": "u"},
    )
    assert r.status_code == 429
    assert "جرّب بعد ساعة" in r.json()["detail"]
    await c.aclose()


async def test_global_chat_minute_limit(tmp_path):
    c = await make_client(low_limit_settings(tmp_path, global_chat_per_minute=1))
    token = await register_user(c)
    r1 = await c.post("/v1/chat", json={"message": "hi"}, headers=auth_headers(token))
    assert r1.status_code == 200
    r2 = await c.post("/v1/chat", json={"message": "hi"}, headers=auth_headers(token))
    assert r2.status_code == 429
    assert "مشغولة" in r2.json()["detail"]
    await c.aclose()


async def test_global_chat_daily_cap(tmp_path):
    c = await make_client(low_limit_settings(tmp_path, global_chat_per_day=1))
    token = await register_user(c)
    r1 = await c.post("/v1/chat", json={"message": "hi"}, headers=auth_headers(token))
    assert r1.status_code == 200
    r2 = await c.post("/v1/chat", json={"message": "hi"}, headers=auth_headers(token))
    assert r2.status_code == 429
    assert "بكرة" in r2.json()["detail"]
    await c.aclose()


async def test_memory_toggle_disables_brain(tmp_path):
    c = await make_client(low_limit_settings(tmp_path))
    token = await register_user(c)
    async with c.app.state.session_factory() as s:
        uid = (await s.execute(select(User))).scalar_one().id
        s.add(MemoryItem(user_id=uid, kind="preference", content="يفضل القهوة سادة"))
        await s.commit()
    r = await c.put(
        "/v1/settings", json={"memory_enabled": False}, headers=auth_headers(token)
    )
    assert r.status_code == 200 and r.json()["memory_enabled"] is False
    async with c.app.state.session_factory() as s:
        assert await memory_block(s, uid) == ""
    router = make_router(FakeProvider(reply='["fact"]'))
    await auto_learn(c.app.state.session_factory, router, uid, "hi", "yo")
    async with c.app.state.session_factory() as s:
        autos = (
            (await s.execute(select(MemoryItem).where(MemoryItem.kind == "auto")))
            .scalars()
            .all()
        )
    assert autos == []
    # Chat works and stays silent about memories while disabled.
    provider = list(c.app.state.provider_router.providers.values())[0]
    r = await c.post("/v1/chat", json={"message": "قولي نكتة"}, headers=auth_headers(token))
    assert r.status_code == 200
    assert "يفضل القهوة سادة" not in provider.calls_log[0][0]["content"]
    await c.aclose()


async def test_sensitive_facts_never_stored(tmp_path):
    c = await make_client(low_limit_settings(tmp_path))
    token = await register_user(c)
    async with c.app.state.session_factory() as s:
        uid = (await s.execute(select(User))).scalar_one().id
    assert _looks_sensitive("كلمة السر بتاعتي hunter2")
    assert _looks_sensitive("my api key is AIzaSyA1234567890abcdefghij")
    assert _looks_sensitive("التوكن: xoxb-123-abc")
    assert not _looks_sensitive("يفضل الردود المختصرة")
    router = make_router(
        FakeProvider(
            reply='["كلمة السر بتاعتي hunter2", "يفضل الردود المختصرة", "sk-abcdef1234567890"]'
        )
    )
    await auto_learn(c.app.state.session_factory, router, uid, "hi", "yo")
    async with c.app.state.session_factory() as s:
        items = (
            (await s.execute(select(MemoryItem).where(MemoryItem.user_id == uid)))
            .scalars()
            .all()
        )
    assert [i.content for i in items] == ["يفضل الردود المختصرة"]
    await c.aclose()


async def test_memory_edit_endpoint(tmp_path):
    c = await make_client(low_limit_settings(tmp_path))
    token = await register_user(c)
    r = await c.post(
        "/v1/memory",
        json={"kind": "preference", "content": "قبل التعديل"},
        headers=auth_headers(token),
    )
    item_id = r.json()["id"]
    r = await c.put(
        f"/v1/memory/{item_id}",
        json={"content": "بعد التعديل"},
        headers=auth_headers(token),
    )
    assert r.status_code == 200 and r.json()["content"] == "بعد التعديل"
    r = await c.put(
        "/v1/memory/nonexistent",
        json={"content": "x"},
        headers=auth_headers(token),
    )
    assert r.status_code == 404
    await c.aclose()
