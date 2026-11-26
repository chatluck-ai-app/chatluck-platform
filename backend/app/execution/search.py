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


_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def _unwrap_ddg(href: str) -> str:
    if "uddg=" in href:
        return urllib.parse.unquote(
            urllib.parse.parse_qs(urllib.parse.urlparse(href).query)["uddg"][0]
        )
    return href


async def _duckduckgo_html(
    query: str, client: httpx.AsyncClient, limit: int
) -> list[SearchResult]:
    resp = await client.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers=_UA,
    )
    resp.raise_for_status()
    results: list[SearchResult] = []
    # Result links: <a class="result__a" href="//duckduckgo.com/l/?uddg=...">
    links = re.findall(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', resp.text, re.S
    )
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', resp.text, re.S)
    for i, (href, title_html) in enumerate(links[:limit]):
        snippet = _strip_tags(snippets[i]) if i < len(snippets) else ""
        results.append(
            SearchResult(
                title=_strip_tags(title_html),
                url=_unwrap_ddg(href),
                snippet=snippet,
                source="duckduckgo",
            )
        )
    if not results:
        raise RuntimeError("duckduckgo html: no results (bot-blocked?)")
    return results


async def _duckduckgo_lite(
    query: str, client: httpx.AsyncClient, limit: int
) -> list[SearchResult]:
    resp = await client.get(
        "https://lite.duckduckgo.com/lite/",
        params={"q": query},
        headers=_UA,
    )
    resp.raise_for_status()
    links = re.findall(
        r'<a rel="nofollow" href="([^"]+)"[^>]*>(.*?)</a>', resp.text, re.S
    )
    results = [
        SearchResult(
            title=_strip_tags(t),
            url=_unwrap_ddg(h),
            snippet="",
            source="duckduckgo",
        )
        for h, t in links[:limit]
    ]
    if not results:
        raise RuntimeError("duckduckgo lite: no results (bot-blocked?)")
    return results


async def _mojeek(
    query: str, client: httpx.AsyncClient, limit: int
) -> list[SearchResult]:
    resp = await client.get(
        "https://www.mojeek.com/search",
        params={"q": query},
        headers=_UA,
    )
    resp.raise_for_status()
    # results: <a class="ob" href="URL">TITLE</a> ... <p class="s">snippet</p>
    blocks = re.findall(
        r'<a class="ob" href="([^"]+)"[^>]*>(.*?)</a>(.*?)</li>',
        resp.text,
        re.S,
    )
    results: list[SearchResult] = []
    for href, title_html, tail in blocks[:limit]:
        m = re.search(r'<p class="s">(.*?)</p>', tail, re.S)
        results.append(
            SearchResult(
                title=_strip_tags(title_html),
                url=href,
                snippet=_strip_tags(m.group(1)) if m else "",
                source="mojeek",
            )
        )
    if not results:
        raise RuntimeError("mojeek: no results")
    return results


async def _wikipedia(
    query: str, client: httpx.AsyncClient, limit: int, lang: str = "ar"
) -> list[SearchResult]:
    wiki_ua = {
        "User-Agent": "ChatLuckAI/0.1 (https://github.com/chatluck-ai-app/chatluck-platform; contact: chatluck-ai-app@users.noreply.github.com)"
    }
    # Full-text search first (good recall for multi-word queries),
    # then opensearch (prefix suggestions) as a fallback.
    resp = await client.get(
        f"https://{lang}.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": str(limit),
            "format": "json",
        },
        headers=wiki_ua,
    )
    resp.raise_for_status()
    hits = resp.json().get("query", {}).get("search", [])
    if hits:
        return [
            SearchResult(
                title=h["title"],
                url=f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(h['title'].replace(' ', '_'))}",
                snippet=_strip_tags(h.get("snippet", "")),
                source="wikipedia",
            )
            for h in hits
        ]
    resp = await client.get(
        f"https://{lang}.wikipedia.org/w/api.php",
        params={
            "action": "opensearch",
            "search": query,
            "limit": str(limit),
            "namespace": "0",
            "format": "json",
        },
        headers=wiki_ua,
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
        for fetch in (_duckduckgo_html, _duckduckgo_lite, _mojeek):
            try:
                results = await fetch(query, client, limit)
                if results:
                    break
            except Exception:
                continue
        if include_wikipedia:
            for lang in ("ar", "en"):
                try:
                    wiki = await _wikipedia(
                        query, client, max(2, limit // 2), lang=lang
                    )
                    results += wiki
                    if wiki:
                        break
                except Exception:
                    continue
        return results[: limit + max(2, limit // 2)]
    finally:
        if own:
            await client.aclose()
