import pytest

from app.main import create_app
from app.providers.router import ProviderRouter  # noqa: F401


def _sys(provider):
    out = []
    for call in provider.calls_log:
        m = call[0]
        out.append(m["content"] if isinstance(m, dict) else m.content)
    return "\n".join(out)

from conftest import auth_headers, register_user


@pytest.mark.asyncio
async def test_archive_and_filter(client):
    async with client.app.router.lifespan_context(client.app):
        t = await register_user(client)
        h = auth_headers(t)
        r = await client.post("/v1/chat", json={"message": "one"}, headers=h)
        cid = r.json()["conversation_id"]
        assert len((await client.get("/v1/conversations", headers=h)).json()) == 1
        r = await client.patch(f"/v1/conversations/{cid}", json={"archived": True}, headers=h)
        assert r.status_code == 200 and r.json()["archived"] is True
        assert (await client.get("/v1/conversations", headers=h)).json() == []
        arch = (await client.get("/v1/conversations?archived=true", headers=h)).json()
        assert len(arch) == 1 and arch[0]["title"] == "one"
        r = await client.patch(f"/v1/conversations/{cid}", json={"archived": False}, headers=h)
        assert len((await client.get("/v1/conversations", headers=h)).json()) == 1
        assert (await client.patch(f"/v1/conversations/{cid}", json={"archived": "x"}, headers=h)).status_code == 422
        assert (await client.patch(f"/v1/conversations/{cid}", json={}, headers=h)).status_code == 422


@pytest.mark.asyncio
async def test_assistant_crud_and_prompt(client):
    async with client.app.router.lifespan_context(client.app):
        t = await register_user(client)
        h = auth_headers(t)
        r = await client.post("/v1/assistants", json={"name": "مترجم", "instructions": "ترجم كل حاجة للإنجليزي"}, headers=h)
        assert r.status_code == 200
        aid = r.json()["id"]
        assert (await client.post("/v1/assistants", json={"name": ""}, headers=h)).status_code == 422
        r = await client.post("/v1/chat", json={"message": "اهلا", "assistant_id": aid}, headers=h)
        assert r.status_code == 200
        conv = (await client.get("/v1/conversations", headers=h)).json()[0]
        assert conv["assistant_id"] == aid
        system_text = _sys(client.app.state.fake)
        assert "ترجم كل حاجة للإنجليزي" in system_text and "مترجم" in system_text
        r = await client.put(f"/v1/assistants/{aid}", json={"name": "مترجم 2"}, headers=h)
        assert r.json()["name"] == "مترجم 2" and r.json()["instructions"] == "ترجم كل حاجة للإنجليزي"
        assert (await client.delete(f"/v1/assistants/{aid}", headers=h)).status_code == 204
        assert (await client.get("/v1/conversations", headers=h)).json()[0]["assistant_id"] is None
        assert (await client.get("/v1/assistants", headers=h)).json() == []


@pytest.mark.asyncio
async def test_assistant_isolated_between_users(client):
    async with client.app.router.lifespan_context(client.app):
        a = auth_headers(await register_user(client, "a@example.com"))
        b = auth_headers(await register_user(client, "b@example.com"))
        aid = (await client.post("/v1/assistants", json={"name": "x"}, headers=a)).json()["id"]
        assert (await client.put(f"/v1/assistants/{aid}", json={"name": "y"}, headers=b)).status_code == 404
        assert (await client.delete(f"/v1/assistants/{aid}", headers=b)).status_code == 404
        r = await client.post("/v1/chat", json={"message": "hi", "assistant_id": aid}, headers=b)
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_share_read_only_and_revoke(client):
    async with client.app.router.lifespan_context(client.app):
        h = auth_headers(await register_user(client, "a@example.com"))
        other = auth_headers(await register_user(client, "b@example.com"))
        cid = (await client.post("/v1/chat", json={"message": "سر"}, headers=h)).json()["conversation_id"]
        assert (await client.post(f"/v1/conversations/{cid}/share", headers=other)).status_code == 404
        tok = (await client.post(f"/v1/conversations/{cid}/share", headers=h)).json()["token"]
        assert (await client.post(f"/v1/conversations/{cid}/share", headers=h)).json()["token"] == tok
        r = await client.get(f"/v1/shared/{tok}")  # no auth
        assert r.status_code == 200
        body = r.json()
        assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
        txt = r.text
        assert "a@example.com" not in txt and "user_id" not in txt
        assert (await client.get("/v1/shared/short")).status_code == 404
        assert (await client.delete(f"/v1/conversations/{cid}/share", headers=h)).status_code == 204
        assert (await client.get(f"/v1/shared/{tok}")).status_code == 404


@pytest.mark.asyncio
async def test_delete_account_removes_assistants(client):
    async with client.app.router.lifespan_context(client.app):
        g = (await client.post("/v1/auth/guest")).json()["access_token"]
        h = auth_headers(g)
        await client.post("/v1/assistants", json={"name": "x"}, headers=h)
        assert (await client.request("DELETE", "/v1/auth/me", json={}, headers=h)).status_code == 204
        from sqlalchemy import func, select
        from app.models import Assistant
        async with client.app.state.session_factory() as s:
            assert (await s.execute(select(func.count()).select_from(Assistant))).scalar_one() == 0
