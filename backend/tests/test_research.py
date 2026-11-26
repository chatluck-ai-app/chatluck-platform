import httpx
import pytest

from app.execution.search import search_web
from conftest import auth_headers, register_user

pytestmark = pytest.mark.asyncio


DDG_HTML = """
<html><body>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">نتيجة أولى</a>
<a class="result__snippet" href="#">ملخص <b>الأولى</b> هنا</a>
<a class="result__a" href="https://example.org/b">نتيجة ثانية</a>
<a class="result__snippet" href="#">ملخص الثانية</a>
</body></html>
"""

WIKI_JSON = ["q", ["عنوان ويكي"], ["وصف ويكي"], ["https://ar.wikipedia.org/wiki/x"]]
WIKI_FULLTEXT_JSON = {
    "query": {
        "search": [
            {
                "title": "عنوان ويكي",
                "snippet": "وصف ويكي",
            }
        ]
    }
}


def wiki_response(request: httpx.Request) -> httpx.Response:
    """Full-text API gets the new shape; opensearch gets the legacy array."""
    if "list=search" in str(request.url):
        return httpx.Response(200, json=WIKI_FULLTEXT_JSON)
    return httpx.Response(200, json=WIKI_JSON)


def make_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_duckduckgo_parsing():
    def handler(request: httpx.Request) -> httpx.Response:
        if "duckduckgo" in str(request.url):
            return httpx.Response(200, text=DDG_HTML)
        return wiki_response(request)

    results = await search_web("test", client=make_client(handler))
    ddg = [r for r in results if r.source == "duckduckgo"]
    assert len(ddg) == 2
    assert ddg[0].url == "https://example.com/a"
    assert ddg[0].title == "نتيجة أولى"
    assert ddg[0].snippet == "ملخص الأولى هنا"


async def test_wikipedia_fallback_when_ddg_fails():
    def handler(request: httpx.Request) -> httpx.Response:
        if "duckduckgo" in str(request.url):
            return httpx.Response(500)
        return wiki_response(request)

    results = await search_web("test", client=make_client(handler))
    assert len(results) == 1
    assert results[0].source == "wikipedia"
    assert results[0].url == "https://ar.wikipedia.org/wiki/%D8%B9%D9%86%D9%88%D8%A7%D9%86_%D9%88%D9%8A%D9%83%D9%8A"


async def test_research_endpoint_with_mocked_search(client, monkeypatch):
    from app import api_research
    from app.execution.search import SearchResult

    async def fake_search(query, limit=5):
        return [
            SearchResult(
                title="مصدر", url="https://example.com", snippet="معلومة", source="duckduckgo"
            )
        ]

    monkeypatch.setattr(api_research, "search_web", fake_search)
    token = await register_user(client, "research@example.com")
    resp = await client.post(
        "/v1/research",
        json={"query": "ما هي عاصمة اليابان؟"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["answer"]
    assert data["sources"][0]["url"] == "https://example.com"


async def test_research_503_when_no_results(client, monkeypatch):
    from app import api_research

    async def fake_search(query, limit=5):
        return []

    monkeypatch.setattr(api_research, "search_web", fake_search)
    token = await register_user(client, "research2@example.com")
    resp = await client.post(
        "/v1/research",
        json={"query": "شيء غير موجود"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 503
