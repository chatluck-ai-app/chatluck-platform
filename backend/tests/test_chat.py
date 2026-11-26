import pytest

from conftest import auth_headers, register_user

pytestmark = pytest.mark.asyncio


async def test_chat_creates_conversation_and_persists(client):
    token = await register_user(client)
    resp = await client.post(
        "/v1/chat",
        json={"message": "عامل ايه؟"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["content"] == "أهلاً! أنا شات لاك."
    assert data["provider"] == "fake"

    conv_id = data["conversation_id"]
    msgs = await client.get(
        f"/v1/conversations/{conv_id}/messages", headers=auth_headers(token)
    )
    assert msgs.status_code == 200
    body = msgs.json()
    assert [m["role"] for m in body] == ["user", "assistant"]
    assert body[0]["content"] == "عامل ايه؟"


async def test_chat_requires_auth(client):
    resp = await client.post("/v1/chat", json={"message": "hi"})
    assert resp.status_code == 401


async def test_conversation_isolation_between_users(client):
    token_a = await register_user(client, "a@example.com")
    token_b = await register_user(client, "b@example.com")
    resp = await client.post(
        "/v1/chat", json={"message": "private"}, headers=auth_headers(token_a)
    )
    conv_id = resp.json()["conversation_id"]

    resp = await client.get(
        f"/v1/conversations/{conv_id}/messages", headers=auth_headers(token_b)
    )
    assert resp.status_code == 404

    resp = await client.post(
        "/v1/chat",
        json={"message": "intrude", "conversation_id": conv_id},
        headers=auth_headers(token_b),
    )
    assert resp.status_code == 404

    convs = await client.get("/v1/conversations", headers=auth_headers(token_b))
    assert convs.json() == []


async def test_chat_stream_sse(client):
    token = await register_user(client)
    resp = await client.post(
        "/v1/chat",
        json={"message": "قولي أهلا", "stream": True},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert "event: token" in resp.text
    assert "event: done" in resp.text

    convs = await client.get("/v1/conversations", headers=auth_headers(token))
    conv_id = convs.json()[0]["id"]
    msgs = await client.get(
        f"/v1/conversations/{conv_id}/messages", headers=auth_headers(token)
    )
    roles = [m["role"] for m in msgs.json()]
    assert roles == ["user", "assistant"]
    assert "شات لاك" in msgs.json()[1]["content"]


async def test_models_endpoint_lists_capabilities(client):
    token = await register_user(client)
    resp = await client.get("/v1/models", headers=auth_headers(token))
    assert resp.status_code == 200
    models = resp.json()
    assert any(m["provider"] == "fake" and m["available"] for m in models)


async def test_image_generation_endpoint(client):
    token = await register_user(client)
    resp = await client.post(
        "/v1/images", json={"prompt": "قطة برتقالية"}, headers=auth_headers(token)
    )
    # No image provider registered in the default test app: honest 503.
    assert resp.status_code in (200, 503)


async def test_memory_crud_and_export(client):
    token = await register_user(client)
    resp = await client.post(
        "/v1/memory",
        json={"kind": "preference", "content": "يفضل الردود بالمصري"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200
    item_id = resp.json()["id"]

    resp = await client.get("/v1/memory", headers=auth_headers(token))
    assert len(resp.json()) == 1

    resp = await client.get("/v1/memory/export", headers=auth_headers(token))
    assert resp.json()["items"][0]["content"] == "يفضل الردود بالمصري"

    resp = await client.delete(f"/v1/memory/{item_id}", headers=auth_headers(token))
    assert resp.status_code == 204
    resp = await client.get("/v1/memory", headers=auth_headers(token))
    assert resp.json() == []


async def test_memory_isolation(client):
    token_a = await register_user(client, "mem-a@example.com")
    token_b = await register_user(client, "mem-b@example.com")
    resp = await client.post(
        "/v1/memory",
        json={"kind": "preference", "content": "secret pref"},
        headers=auth_headers(token_a),
    )
    item_id = resp.json()["id"]
    resp = await client.delete(f"/v1/memory/{item_id}", headers=auth_headers(token_b))
    assert resp.status_code == 404


async def test_conversation_rename_and_delete(client):
    token = await register_user(client, "conv-mgmt@test.com")
    h = {"Authorization": f"Bearer {token}"}
    resp = await client.post(
        "/v1/chat", json={"message": "hello there"}, headers=h
    )
    conv_id = resp.json()["conversation_id"]
    resp = await client.patch(
        f"/v1/conversations/{conv_id}", json={"title": "عنوان جديد"}, headers=h
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "عنوان جديد"
    resp = await client.delete(f"/v1/conversations/{conv_id}", headers=h)
    assert resp.status_code == 204
    resp = await client.get("/v1/conversations", headers=h)
    assert all(c["id"] != conv_id for c in resp.json())


async def test_conversation_search(client):
    token = await register_user(client, "search@test.com")
    h = {"Authorization": f"Bearer {token}"}
    await client.post("/v1/chat", json={"message": "عايز أتعلم بايثون"}, headers=h)
    await client.post("/v1/chat", json={"message": "وصفة كشري"}, headers=h)

    resp = await client.get("/v1/conversations/search", params={"q": "بايثون"}, headers=h)
    assert resp.status_code == 200, resp.text
    results = resp.json()
    assert len(results) == 1
    assert "بايثون" in results[0]["snippet"]

    resp = await client.get("/v1/conversations/search", params={"q": "غيرموجود"}, headers=h)
    assert resp.json() == []


async def test_regenerate_replaces_last_answer(client):
    token = await register_user(client, "regen@test.com")
    h = {"Authorization": f"Bearer {token}"}
    resp = await client.post("/v1/chat", json={"message": "سؤال أول"}, headers=h)
    conv_id = resp.json()["conversation_id"]

    resp = await client.post(
        "/v1/chat",
        json={"message": "سؤال أول", "conversation_id": conv_id, "regenerate": True},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    msgs = await client.get(f"/v1/conversations/{conv_id}/messages", headers=h)
    body = msgs.json()
    assert [m["role"] for m in body] == ["user", "assistant"]
    assert body[0]["content"] == "سؤال أول"


async def test_edit_message_drops_later_messages(client):
    token = await register_user(client, "edit@test.com")
    h = {"Authorization": f"Bearer {token}"}
    resp = await client.post("/v1/chat", json={"message": "رسالة أولى"}, headers=h)
    conv_id = resp.json()["conversation_id"]
    await client.post(
        "/v1/chat", json={"message": "رسالة ثانية", "conversation_id": conv_id}, headers=h
    )
    msgs = await client.get(f"/v1/conversations/{conv_id}/messages", headers=h)
    first_user_id = msgs.json()[0]["id"]

    resp = await client.post(
        "/v1/chat",
        json={
            "message": "رسالة معدلة",
            "conversation_id": conv_id,
            "edit_message_id": first_user_id,
        },
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    msgs = await client.get(f"/v1/conversations/{conv_id}/messages", headers=h)
    body = msgs.json()
    assert [m["role"] for m in body] == ["user", "assistant"]
    assert body[0]["id"] == first_user_id
    assert body[0]["content"] == "رسالة معدلة"


async def test_edit_rejects_other_users_message(client):
    token_a = await register_user(client, "edit-a@test.com")
    token_b = await register_user(client, "edit-b@test.com")
    resp = await client.post(
        "/v1/chat", json={"message": "msg"}, headers=auth_headers(token_a)
    )
    conv_id = resp.json()["conversation_id"]
    resp = await client.post(
        "/v1/chat",
        json={"message": "hack", "conversation_id": conv_id, "edit_message_id": "x"},
        headers=auth_headers(token_b),
    )
    assert resp.status_code == 404


async def test_friendly_error_mapping():
    from app.api_chat import friendly_error

    assert "مشغولة" in friendly_error("gemini: Gemini HTTP 503: high demand")
    assert "استنى دقيقة" in friendly_error("HTTP 429 rate limit exceeded")
    assert "مزود" in friendly_error("all providers failed -> x")
    assert "غير متوقع" in friendly_error("something bizarre")
