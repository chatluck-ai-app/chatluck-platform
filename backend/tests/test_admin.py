import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.db import init_db
from app.main import create_app
from app.rate_limit import RateLimiter

from conftest import FakeProvider, auth_headers, make_router, register_user


@pytest_asyncio.fixture()
async def admin_client(settings):
    settings.admin_emails = "boss@example.com"
    router = make_router(FakeProvider())
    app = create_app(settings, provider_router=router, rate_limiter=RateLimiter(1000))
    await init_db(app.state.engine)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_admin_stats_requires_auth(admin_client):
    resp = await admin_client.get("/v1/admin/stats")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_admin_stats_forbidden_for_regular_user(admin_client):
    token = await register_user(admin_client, email="regular@example.com")
    resp = await admin_client.get("/v1/admin/stats", headers=auth_headers(token))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_stats_ok_for_admin(admin_client):
    admin_token = await register_user(admin_client, email="boss@example.com")
    await register_user(admin_client, email="someone@example.com")
    # one chat message so message counts are non-zero
    await admin_client.post(
        "/v1/chat",
        json={"message": "أهلاً"},
        headers=auth_headers(admin_token),
    )
    resp = await admin_client.get("/v1/admin/stats", headers=auth_headers(admin_token))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["users_total"] == 2
    assert data["messages_total"] >= 2  # user + assistant
    assert data["conversations_total"] >= 1
    assert isinstance(data["by_model"], list)
    assert len(data["recent_users"]) == 2
    assert data["recent_users"][0]["email"] == "someone@example.com"
