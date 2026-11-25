"""Shared client for OpenAI-compatible APIs: Groq, OpenRouter, and any future
provider exposing /chat/completions (including OpenAI itself)."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from .base import (
    CHAT,
    STREAM,
    TOOLS,
    BaseProvider,
    Message,
    ModelInfo,
    ProviderError,
)


class OpenAICompatibleProvider(BaseProvider):
    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str | None,
        model_list: list[ModelInfo],
        timeout: float = 60.0,
        default_model: str | None = None,
        extra_headers: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._models = model_list
        self.timeout = timeout
        self._default_model = default_model or (
            model_list[0].id if model_list else ""
        )
        self.extra_headers = extra_headers or {}
        self._client = client

    def models(self) -> list[ModelInfo]:
        return self._models

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", **self.extra_headers}

    def _body(self, messages: list[Message], model: str, stream: bool) -> dict[str, Any]:
        return {"model": model, "messages": messages, "stream": stream}

    async def chat(
        self, messages: list[Message], model: str | None = None, **kw: Any
    ) -> str:
        if not self.api_key:
            raise ProviderError(f"{self.name} API key not configured", self.name, False)
        model = model or self._default_model
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        close = self._client is None
        try:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=self._body(messages, model, False),
            )
        finally:
            if close:
                await client.aclose()
        if resp.status_code != 200:
            raise ProviderError(
                f"{self.name} HTTP {resp.status_code}: {resp.text[:200]}",
                self.name,
                retryable=resp.status_code in (429, 500, 502, 503, 504),
                status=resp.status_code,
            )
        try:
            return resp.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                f"unexpected {self.name} response shape", self.name
            ) from exc

    async def chat_stream(
        self, messages: list[Message], model: str | None = None, **kw: Any
    ) -> AsyncIterator[str]:
        if not self.api_key:
            raise ProviderError(f"{self.name} API key not configured", self.name, False)
        model = model or self._default_model
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        close = self._client is None
        try:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=self._body(messages, model, True),
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise ProviderError(
                        f"{self.name} HTTP {resp.status_code}: {body[:200]!r}",
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
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0]["delta"].get("content")
                    except (ValueError, KeyError, IndexError, TypeError):
                        continue
                    if delta:
                        yield delta
        finally:
            if close:
                await client.aclose()


def make_groq(
    api_key: str | None,
    timeout: float = 60.0,
    client: httpx.AsyncClient | None = None,
) -> OpenAICompatibleProvider:
    models = [
        ModelInfo(
            id="llama-3.3-70b-versatile",
            provider="groq",
            capabilities=frozenset({CHAT, STREAM, TOOLS}),
            context_window=131_072,
        ),
        ModelInfo(
            id="llama-3.1-8b-instant",
            provider="groq",
            capabilities=frozenset({CHAT, STREAM}),
            context_window=131_072,
        ),
        ModelInfo(
            id="whisper-large-v3",
            provider="groq",
            capabilities=frozenset({"stt"}),
        ),
    ]
    return OpenAICompatibleProvider(
        "groq",
        "https://api.groq.com/openai/v1",
        api_key,
        models,
        timeout=timeout,
        client=client,
    )


def make_openrouter(
    api_key: str | None,
    timeout: float = 60.0,
    client: httpx.AsyncClient | None = None,
) -> OpenAICompatibleProvider:
    models = [
        ModelInfo(
            id="meta-llama/llama-3.3-70b-instruct:free",
            provider="openrouter",
            capabilities=frozenset({CHAT, STREAM}),
            context_window=131_072,
        ),
        ModelInfo(
            id="google/gemma-3-27b-it:free",
            provider="openrouter",
            capabilities=frozenset({CHAT, STREAM}),
            context_window=131_072,
        ),
    ]
    return OpenAICompatibleProvider(
        "openrouter",
        "https://openrouter.ai/api/v1",
        api_key,
        models,
        timeout=timeout,
        extra_headers={"X-OpenRouter-Title": "Chat Luck AI"},
        client=client,
    )
