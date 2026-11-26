"""Free web search: DuckDuckGo instant answers + Wikipedia, no keys required.
Returns structured results with source URLs; callers must cite them."""
from __future__ import annotations

import asyncio
import html
import logging
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


_FILLER = re.compile(
    r"(إيه|ايه|إزاي|ازاي|ليه|لماذا|بكام|كام|متى|فين|منين|النهاردة|النهارده|دلوقتي|"
    r"حالا|حالاً|من فضلك|لو سمحت|عامل|عاملة|يعني|طب|؟|\?|!|،)",
    re.IGNORECASE,
)


def clean_query(text: str) -> str:
    """Strip Egyptian/Arabic question fillers so search engines see keywords."""
    words = [w for w in _FILLER.sub(" ", text).split() if len(w) > 1]
    return " ".join(words[:10]) or text.strip()


logger = logging.getLogger("chatluck.search")

# Per-engine budget. Datacenter IPs often get silently hung by DDG/Mojeek:
# without a tight per-call timeout one blocked engine eats the whole search
# budget and Wikipedia (the reliable path) never runs.
_ENGINE_TIMEOUT = 2.5


async def _engine_chain(query, client, limit):
    for fetch in (_duckduckgo_html, _duckduckgo_lite, _mojeek):
        try:
            results = await fetch(query, client, limit)
            if results:
                return results
        except Exception as exc:
            logger.warning(
                "search engine %s failed: %s: %s",
                fetch.__name__, type(exc).__name__, str(exc)[:120],
            )
    return []


async def _wiki_chain(query, client, limit, include_wikipedia):
    if not include_wikipedia:
        return []
    for lang in ("ar", "en"):
        try:
            wiki = await _wikipedia(query, client, max(2, limit // 2), lang=lang)
            if wiki:
                return wiki
        except Exception as exc:
            logger.warning(
                "wikipedia %s search failed: %s: %s",
                lang, type(exc).__name__, str(exc)[:120],
            )
    return []


def _cap(results: list[SearchResult], limit: int) -> list[SearchResult]:
    return results[: limit + max(2, limit // 2)]


async def search_web(
    query: str,
    limit: int = 5,
    client: httpx.AsyncClient | None = None,
    include_wikipedia: bool = True,
) -> list[SearchResult]:
    cleaned = clean_query(query)
    if client is not None:
        # Shared-client mode (tests/mocks): sequential, client owns timeouts.
        results = await _engine_chain(cleaned, client, limit)
        results += await _wiki_chain(cleaned, client, limit, include_wikipedia)
        return _cap(results, limit)
    # Production: Wikipedia and all engines race in parallel with a hard
    # total budget. A hung engine is cancelled instead of starving Wikipedia.
    _TOTAL_TIMEOUT = 4.0
    async with httpx.AsyncClient(timeout=_ENGINE_TIMEOUT) as shared:
        tasks = [
            ("duckduckgo_html", asyncio.ensure_future(_duckduckgo_html(cleaned, shared, limit))),
            ("duckduckgo_lite", asyncio.ensure_future(_duckduckgo_lite(cleaned, shared, limit))),
            ("mojeek", asyncio.ensure_future(_mojeek(cleaned, shared, limit))),
            ("wikipedia", asyncio.ensure_future(_wiki_chain(cleaned, shared, limit, include_wikipedia))),
        ]
        done, pending = await asyncio.wait(
            [t for _, t in tasks], timeout=_TOTAL_TIMEOUT
        )
        for t in pending:
            t.cancel()
        outcomes: dict[str, object] = {}
        for name, t in tasks:
            if t in pending:
                logger.warning("search source %s cancelled after %.1fs budget", name, _TOTAL_TIMEOUT)
                outcomes[name] = []
                continue
            exc = t.exception()
            outcomes[name] = exc if exc is not None else t.result()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    engines: list[SearchResult] = []
    wiki: list[SearchResult] = []
    for name, outcome in outcomes.items():
        if isinstance(outcome, Exception):
            logger.warning(
                "search source %s failed: %s: %s",
                name, type(outcome).__name__, str(outcome)[:120],
            )
            continue
        if not outcome:
            continue
        if name == "wikipedia":
            wiki = outcome
        elif not engines:
            engines = outcome
    return _cap(engines + wiki, limit)
