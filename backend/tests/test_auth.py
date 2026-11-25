import pytest

from conftest import auth_headers, register_user

pytestmark = pytest.mark.asyncio


async def test_register_and_me(client):
    token = await register_user(client)
    resp = await client.get("/v1/auth/me", headers=auth_headers(token))
    assert resp.status_code == 200
    assert resp.json()["email"] == "hossam@example.com"
    assert resp.json()["display_name"] == "Hossam"


async def test_register_duplicate_email_rejected(client):
    await register_user(client)
    resp = await client.post(
        "/v1/auth/register",
        json={"email": "hossam@example.com", "password": "secret123"},
    )
    assert resp.status_code == 409


async def test_login_success(client):
    await register_user(client)
    resp = await client.post(
        "/v1/auth/login",
        json={"email": "hossam@example.com", "password": "secret123"},
    )
    assert resp.status_code == 200
    assert resp.json()["access_token"]


async def test_login_wrong_password(client):
    await register_user(client)
    resp = await client.post(
        "/v1/auth/login",
        json={"email": "hossam@example.com", "password": "wrong-password"},
    )
    assert resp.status_code == 401


async def test_me_requires_token(client):
    resp = await client.get("/v1/auth/me")
    assert resp.status_code == 401


async def test_me_rejects_garbage_token(client):
    resp = await client.get(
        "/v1/auth/me", headers={"Authorization": "Bearer not-a-token"}
    )
    assert resp.status_code == 401


async def test_short_password_rejected(client):
    resp = await client.post(
        "/v1/auth/register",
        json={"email": "x@example.com", "password": "short"},
    )
    assert resp.status_code == 422
