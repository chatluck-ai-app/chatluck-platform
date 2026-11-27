import pytest
from sqlalchemy import func, select

from app.models import Conversation, Message, UsageRecord, User
from conftest import auth_headers, register_user


async def _set_limits(client, **kw):
    from app.models import PlanLimit
    async with client.app.state.session_factory() as s:
        row = await s.get(PlanLimit, "free")
        for k, v in kw.items():
            setattr(row, k, v)
        await s.commit()


@pytest.mark.asyncio
async def test_plan_endpoint_and_message_quota(client):
    async with client.app.router.lifespan_context(client.app):
        t = await register_user(client)
        await _set_limits(client, daily_messages=2)
        for _ in range(2):
            r = await client.post("/v1/chat", json={"message": "hi"}, headers=auth_headers(t))
            assert r.status_code == 200, r.text
        r = await client.post("/v1/chat", json={"message": "hi"}, headers=auth_headers(t))
        assert r.status_code == 429
        assert "اليومي" in r.json()["detail"]
        p = (await client.get("/v1/plan", headers=auth_headers(t))).json()
        assert p["plan"] == "free" and p["used_today"]["messages"] == 2
        assert p["limits"]["messages"] == 2


@pytest.mark.asyncio
async def test_guest_and_delete(client):
    async with client.app.router.lifespan_context(client.app):
        g = (await client.post("/v1/auth/guest")).json()
        assert g["is_guest"]
        h = auth_headers(g["access_token"])
        assert (await client.post("/v1/chat", json={"message": "hi"}, headers=h)).status_code == 200
        assert (await client.get("/v1/plan", headers=h)).json()["is_guest"] is True
        assert (await client.request("DELETE", "/v1/auth/me", json={}, headers=h)).status_code == 204
        assert (await client.get("/v1/auth/me", headers=h)).status_code == 401
        async with client.app.state.session_factory() as s:
            assert (await s.execute(select(func.count()).select_from(Message))).scalar_one() == 0
            assert (await s.execute(select(func.count()).select_from(Conversation))).scalar_one() == 0
            assert (await s.execute(select(func.count()).select_from(UsageRecord))).scalar_one() == 0


@pytest.mark.asyncio
async def test_delete_requires_password(client):
    async with client.app.router.lifespan_context(client.app):
        t = await register_user(client)
        h = auth_headers(t)
        assert (await client.request("DELETE", "/v1/auth/me", json={"password": "wrong"}, headers=h)).status_code == 401
        assert (await client.get("/v1/auth/me", headers=h)).status_code == 200
        assert (await client.request("DELETE", "/v1/auth/me", json={"password": "secret123"}, headers=h)).status_code == 204
        assert (await client.get("/v1/auth/me", headers=h)).status_code == 401


@pytest.mark.asyncio
async def test_admin_plans_gated_and_editable(client):
    async with client.app.router.lifespan_context(client.app):
        client.app.state.settings.admin_emails = "boss@example.com"
        u = await register_user(client, "user@example.com")
        assert (await client.get("/v1/admin/plans", headers=auth_headers(u))).status_code == 403
        a = await register_user(client, "boss@example.com")
        ha = auth_headers(a)
        assert len((await client.get("/v1/admin/plans", headers=ha)).json()) == 3
        r = await client.put("/v1/admin/plans/free", json={"daily_messages": 1}, headers=ha)
        assert r.status_code == 200 and r.json()["daily_messages"] == 1
        assert (await client.put("/v1/admin/plans/free", json={"daily_messages": -5}, headers=ha)).status_code == 422
        r = await client.put("/v1/admin/users/user@example.com/plan", json={"plan": "pro"}, headers=ha)
        assert r.status_code == 200
        assert (await client.get("/v1/plan", headers=auth_headers(u))).json()["plan"] == "pro"


@pytest.mark.asyncio
async def test_privacy_page(client):
    r = await client.get("/privacy")
    assert r.status_code == 200 and "سياسة الخصوصية" in r.text


def _req(xff, cf=None):
    from starlette.requests import Request
    h = [(b"x-forwarded-for", xff.encode())]
    if cf:
        h.append((b"cf-connecting-ip", cf.encode()))
    return Request({"type": "http", "headers": h, "client": ("10.0.0.1", 1)})


def test_client_ip_chain():
    from app.client_ip import client_ip
    assert client_ip(_req("1.2.3.4, 172.64.1.1, 10.1.1.1")) == "1.2.3.4"
    # forged leftmost entry cannot change the derived client
    assert client_ip(_req("9.9.9.9, 1.2.3.4, 172.64.1.1, 10.1.1.1")) == "1.2.3.4"
    # no Cloudflare hop -> shared bucket, never trust header
    assert client_ip(_req("1.2.3.4, 5.6.7.8")) == "unknown"
    assert client_ip(_req("garbage")) == "unknown"
