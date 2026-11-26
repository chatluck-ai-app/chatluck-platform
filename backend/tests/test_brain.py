import asyncio

import pytest
from sqlalchemy import select

from app.brain import auto_learn, memory_block, parse_facts
from app.models import MemoryItem
from conftest import FakeProvider, auth_headers, make_router, register_user

pytestmark = pytest.mark.asyncio


async def test_parse_facts_robust():
    assert parse_facts('["أ", "ب"]') == ["أ", "ب"]
    assert parse_facts('prefix ["x"] suffix') == ["x"]
    assert parse_facts("no json") == []
    assert parse_facts('{"a": 1}') == []
    assert parse_facts("[1, 2]") == []


async def test_memory_block_empty_and_filled(client):
    token = await register_user(client)
    async with client.app.state.session_factory() as s:
        from app.models import User

        uid = (await s.execute(select(User))).scalar_one().id
        assert await memory_block(s, uid) == ""
        s.add(MemoryItem(user_id=uid, kind="preference", content="يحب الإيجاز"))
        await s.commit()
        block = await memory_block(s, uid)
        assert "يحب الإيجاز" in block
        assert "ذاكرتك" in block


async def test_auto_learn_stores_and_dedupes(client):
    token = await register_user(client)
    async with client.app.state.session_factory() as s:
        from app.models import User

        uid = (await s.execute(select(User))).scalar_one().id
    router = make_router(FakeProvider(reply='["اسمه حسام", "يفضل المصرية"]'))
    await auto_learn(client.app.state.session_factory, router, uid, "أنا حسام", "أهلاً حسام")
    async with client.app.state.session_factory() as s:
        items = (
            (await s.execute(select(MemoryItem).where(MemoryItem.user_id == uid)))
            .scalars()
            .all()
        )
    assert sorted(i.content for i in items) == ["اسمه حسام", "يفضل المصرية"]
    assert all(i.kind == "auto" for i in items)
    # Second run with the same facts adds nothing.
    await auto_learn(client.app.state.session_factory, router, uid, "أنا حسام", "أهلاً حسام")
    async with client.app.state.session_factory() as s:
        again = (
            (await s.execute(select(MemoryItem).where(MemoryItem.user_id == uid)))
            .scalars()
            .all()
        )
    assert len(again) == 2


async def test_auto_learn_never_raises(client):
    router = make_router(FakeProvider(reply="garbage not json", fail_times=0))
    await auto_learn(client.app.state.session_factory, router, "nobody", "hi", "yo")
    broken = make_router(FakeProvider(fail_times=99))
    await auto_learn(client.app.state.session_factory, broken, "nobody", "hi", "yo")


async def test_chat_injects_memory_into_system_prompt(client):
    token = await register_user(client)
    async with client.app.state.session_factory() as s:
        from app.models import User

        uid = (await s.execute(select(User))).scalar_one().id
        s.add(MemoryItem(user_id=uid, kind="preference", content="بيحب الردود القصيرة"))
        await s.commit()
    provider = list(client.app.state.provider_router.providers.values())[0]
    resp = await client.post(
        "/v1/chat",
        json={"message": "قولي نكتة"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200, resp.text
    # First call is the chat itself; later calls are background learning.
    system_msg = provider.calls_log[0][0]
    assert system_msg["role"] == "system"
    assert "بيحب الردود القصيرة" in system_msg["content"]


async def test_stream_chat_schedules_auto_learn(client):
    token = await register_user(client)
    resp = await client.post(
        "/v1/chat",
        json={"message": "أنا بحب القهوة سادة", "stream": True},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200, resp.text
    assert "event: done" in resp.text
    # FakeProvider replies are not JSON, so learning stores nothing — but the
    # background task must run without breaking the stream. Give it a beat.
    await asyncio.sleep(0.1)
