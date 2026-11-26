import pytest

import app.api_chat as api_chat
from app.execution.search import SearchResult
from conftest import FakeProvider, auth_headers, register_user

pytestmark = pytest.mark.asyncio


def fake_results():
    return [
        SearchResult(
            title="سعر الدولار اليوم",
            url="https://example.com/dollar",
            snippet="الدولار يسجل ٥٠ جنيه",
            source="wikipedia",
        )
    ]


async def test_heuristic_matches_only_current_fact_queries():
    assert api_chat._NEEDS_SEARCH.search("إيه سعر الدولار النهاردة؟")
    assert api_chat._NEEDS_SEARCH.search("أخبار مصر دلوقتي")
    assert api_chat._NEEDS_SEARCH.search("what is the weather today")
    assert not api_chat._NEEDS_SEARCH.search("احكيلي نكتة")
    assert not api_chat._NEEDS_SEARCH.search("اكتبلي كود بايثون")


async def test_stream_emits_sources_and_grounds_prompt(client, monkeypatch):
    calls = []

    async def fake_search(query, limit=4):
        calls.append(query)
        return fake_results()

    monkeypatch.setattr(api_chat, "search_web", fake_search)
    token = await register_user(client)
    resp = await client.post(
        "/v1/chat",
        json={"message": "إيه سعر الدولار النهاردة؟", "stream": True},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200, resp.text
    assert "event: sources" in resp.text
    assert "سعر الدولار اليوم" in resp.text
    assert calls  # search ran
    provider = list(client.app.state.provider_router.providers.values())[0]
    system = provider.calls_log[0][0]["content"]
    assert "نتائج بحث حديثة" in system
    assert "الدولار يسجل" in system


async def test_no_search_no_sources_event(client, monkeypatch):
    async def boom(query, limit=4):
        raise AssertionError("search must not run for ordinary chat")

    monkeypatch.setattr(api_chat, "search_web", boom)
    token = await register_user(client)
    resp = await client.post(
        "/v1/chat",
        json={"message": "احكيلي نكتة", "stream": True},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200
    assert "event: sources" not in resp.text


async def test_search_failure_falls_back_to_plain_chat(client, monkeypatch):
    async def boom(query, limit=4):
        raise RuntimeError("network down")

    monkeypatch.setattr(api_chat, "search_web", boom)
    token = await register_user(client)
    resp = await client.post(
        "/v1/chat",
        json={"message": "أخبار مصر النهاردة", "stream": True},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200
    assert "event: sources" not in resp.text
    assert "event: done" in resp.text


async def test_non_stream_includes_sources(client, monkeypatch):
    async def fake_search(query, limit=4):
        return fake_results()

    monkeypatch.setattr(api_chat, "search_web", fake_search)
    token = await register_user(client)
    resp = await client.post(
        "/v1/chat",
        json={"message": "الطقس النهاردة عامل إيه؟"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sources"] and body["sources"][0]["url"] == "https://example.com/dollar"


async def test_clean_query_strips_fillers():
    from app.execution.search import clean_query

    assert clean_query("إيه سعر الدولار النهاردة؟") == "سعر الدولار"
    assert clean_query("عاصمة فرنسا إيه؟ وإيه آخر أخبارها النهاردة") == (
        "عاصمة فرنسا آخر أخبارها"
    )
    assert clean_query("what is the weather today") != ""


async def test_grounding_instruction_is_strict(client, monkeypatch):
    async def fake_search(query, limit=4):
        return fake_results()

    monkeypatch.setattr(api_chat, "search_web", fake_search)
    token = await register_user(client)
    resp = await client.post(
        "/v1/chat",
        json={"message": "الطقس النهاردة عامل إيه؟"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200
    provider = list(client.app.state.provider_router.providers.values())[0]
    system = provider.calls_log[0][0]["content"]
    assert "قواعد صارمة" in system
    assert "متعرضش أحداث أو أخبار من عندك كأنها حالية" in system
