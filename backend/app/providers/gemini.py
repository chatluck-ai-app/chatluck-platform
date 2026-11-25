"""Google Gemini provider (AI Studio free tier). REST, no SDK dependency."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from .base import (
    CHAT,
    STREAM,
    TOOLS,
    VISION,
    BaseProvider,
    Message,
    ModelInfo,
    ProviderError,
)

API_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(BaseProvider):
    name = "gemini"

    def __init__(
        self,
        api_key: str | None,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.api_key = api_key
        self.timeout = timeout
        self._client = client

    def models(self) -> list[ModelInfo]:
        # Verified live against this key on 2026-11-26: 2.5 generation is
        # retired for new users; these generate content successfully.
        return [
            ModelInfo(
                id="gemini-3.1-flash-lite",
                provider=self.name,
                capabilities=frozenset({CHAT, STREAM, VISION}),
                context_window=1_000_000,
            ),
            ModelInfo(
                id="gemini-3-flash-preview",
                provider=self.name,
                capabilities=frozenset({CHAT, STREAM, VISION, TOOLS}),
                context_window=1_000_000,
            ),
            ModelInfo(
                id="gemini-3.8-flash",
                provider=self.name,
                capabilities=frozenset({CHAT, STREAM, VISION, TOOLS}),
                context_window=1_000_000,
            ),
        ]

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def default_model(self) -> str:
        return "gemini-3.1-flash-lite"

    def _payload(self, messages: list[Message]) -> dict[str, Any]:
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = []
        for m in messages:
            if m["role"] == "system":
                continue
            role = "model" if m["role"] == "assistant" else "user"
            parts: list[dict[str, Any]] = []
            for att in m.get("attachments", []):
                parts.append(
                    {
                        "inline_data": {
                            "mime_type": att["mime_type"],
                            "data": att["data_base64"],
                        }
                    }
                )
            parts.append({"text": m["content"]})
            contents.append({"role": role, "parts": parts})
        payload: dict[str, Any] = {"contents": contents}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        return payload

    def _extract(self, data: dict[str, Any]) -> str:
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                f"unexpected Gemini response shape: {str(data)[:200]}",
                self.name,
                retryable=True,
            ) from exc

    async def chat(
        self, messages: list[Message], model: str | None = None, **kw: Any
    ) -> str:
        if not self.api_key:
            raise ProviderError("GEMINI_API_KEY not configured", self.name, False)
        model = model or self.default_model()
        url = f"{API_BASE}/models/{model}:generateContent"
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        close = self._client is None
        try:
            resp = await client.post(
                url,
                params={"key": self.api_key},
                json=self._payload(messages),
            )
        finally:
            if close:
                await client.aclose()
        if resp.status_code != 200:
            raise ProviderError(
                f"Gemini HTTP {resp.status_code}: {resp.text[:200]}",
                self.name,
                retryable=resp.status_code in (429, 500, 502, 503, 504),
                status=resp.status_code,
            )
        return self._extract(resp.json())

    async def chat_stream(
        self, messages: list[Message], model: str | None = None, **kw: Any
    ) -> AsyncIterator[str]:
        if not self.api_key:
            raise ProviderError("GEMINI_API_KEY not configured", self.name, False)
        model = model or self.default_model()
        url = f"{API_BASE}/models/{model}:streamGenerateContent"
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        close = self._client is None
        try:
            async with client.stream(
                "POST",
                url,
                params={"key": self.api_key, "alt": "sse"},
                json=self._payload(messages),
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise ProviderError(
                        f"Gemini HTTP {resp.status_code}: {body[:200]!r}",
                        self.name,
                        retryable=resp.status_code in (429, 500, 502, 503, 504),
                        status=resp.status_code,
                    )
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    chunk = json.loads(data)
                    try:
                        parts = chunk["candidates"][0]["content"]["parts"]
                        text = "".join(p.get("text", "") for p in parts)
                    except (KeyError, IndexError, TypeError):
                        continue
                    if text:
                        yield text
        finally:
            if close:
                await client.aclose()
