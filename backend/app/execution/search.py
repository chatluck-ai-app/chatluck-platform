"""Free web search: DuckDuckGo instant answers + Wikipedia, no keys required.
Returns structured results with source URLs; callers must cite them."""
from __future__ import annotations

import html
import re
import urllib.parse
from dataclasses import dataclass

import httpx


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str  # duckduckgo | wikipedia


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text)).strip()


async def _duckduckgo_html(
    query: str, client: httpx.AsyncClient, limit: int
) -> list[SearchResult]:
    resp = await client.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers={"User-Agent": "Mozilla/5.0 (compatible; ChatLuckAI/0.1)"},
    )
    resp.raise_for_status()
    results: list[SearchResult] = []
    # Result links: <a class="result__a" href="//duckduckgo.com/l/?uddg=...">
    links = re.findall(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', resp.text, re.S
    )
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', resp.text, re.S)
    for i, (href, title_html) in enumerate(links[:limit]):
        url = href
        if "uddg=" in href:
            url = urllib.parse.unquote(
                urllib.parse.parse_qs(urllib.parse.urlparse(href).query)["uddg"][0]
            )
        snippet = _strip_tags(snippets[i]) if i < len(snippets) else ""
        results.append(
            SearchResult(
                title=_strip_tags(title_html),
                url=url,
                snippet=snippet,
                source="duckduckgo",
            )
        )
    return results


async def _wikipedia(query: str, client: httpx.AsyncClient, limit: int) -> list[SearchResult]:
    resp = await client.get(
        "https://ar.wikipedia.org/w/api.php",
        params={
            "action": "opensearch",
            "search": query,
            "limit": str(limit),
            "namespace": "0",
            "format": "json",
        },
        headers={"User-Agent": "ChatLuckAI/0.1 (research tool)"},
    )
    resp.raise_for_status()
    data = resp.json()
    titles, descriptions, urls = data[1], data[2], data[3]
    return [
        SearchResult(title=t, url=u, snippet=d, source="wikipedia")
        for t, d, u in zip(titles, descriptions, urls)
    ]


async def search_web(
    query: str,
    limit: int = 5,
    client: httpx.AsyncClient | None = None,
    include_wikipedia: bool = True,
) -> list[SearchResult]:
    own = client is None
    client = client or httpx.AsyncClient(timeout=20.0)
    try:
        results: list[SearchResult] = []
        try:
            results = await _duckduckgo_html(query, client, limit)
        except Exception:
            results = []
        if include_wikipedia:
            try:
                results += await _wikipedia(query, client, max(2, limit // 2))
            except Exception:
                pass
        return results[: limit + max(2, limit // 2)]
    finally:
        if own:
            await client.aclose()
