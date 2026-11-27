"""Model router: capability-aware selection, automatic fallback, and a
circuit breaker per provider. Manual selection is honored when possible;
automatic routing prefers free, healthy providers."""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

from .base import BaseProvider, Message, ModelInfo, ProviderError


@dataclass
class ProviderHealth:
    failures: int = 0
    open_until: float = 0.0


class AllProvidersDownError(Exception):
    def __init__(self, attempts: list[tuple[str, str]]):
        self.attempts = attempts
        detail = "; ".join(f"{p}: {e}" for p, e in attempts)
        super().__init__(f"all providers failed -> {detail}")


# Latency guards: if a provider stalls before producing anything, fail fast
# and retry/fallback instead of letting the user wait ~a minute on a hung
# request (Gemini "high demand" can hold a stream open with no bytes).
QUOTA_COOLDOWN = 30.0
_FIRST_TOKEN_TIMEOUT = 10.0
_NEXT_TOKEN_TIMEOUT = 30.0


def _backoff(attempt: int) -> float:
    """Jittered exponential backoff: ~0.8-1.2s, then ~1.6-2.4s."""
    return (0.8 * (2 ** (attempt - 1))) * (0.75 + random.random() * 0.5)


class ProviderRouter:
    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: float = 120.0,
        clock=time.monotonic,
    ):
        self.providers: dict[str, BaseProvider] = {}
        self.health: dict[str, ProviderHealth] = {}
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.clock = clock

    def register(self, provider: BaseProvider) -> None:
        self.providers[provider.name] = provider
        self.health.setdefault(provider.name, ProviderHealth())

    # ---- introspection -------------------------------------------------

    def list_models(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for provider in self.providers.values():
            for m in provider.models():
                out.append(
                    {
                        "id": m.id,
                        "provider": m.provider,
                        "capabilities": sorted(m.capabilities),
                        "context_window": m.context_window,
                        "free": m.free,
                        "available": provider.is_configured()
                        and not self._is_open(self._key(provider, m)),
                    }
                )
        return out

    @staticmethod
    def _key(provider: BaseProvider, model: ModelInfo) -> str:
        return f"{provider.name}:{model.id}"

    def _is_open(self, name: str) -> bool:
        """True when `name` (a "provider:model" key, or a provider name whose
        every model is open) is in circuit-breaker cooldown. Health is tracked
        per model: free-tier quotas are per model, so one saturated model must
        not take its sibling models offline."""
        h = self.health.get(name)
        if h is not None and h.failures >= self.failure_threshold and self.clock() < h.open_until:
            return True
        provider = self.providers.get(name)
        if provider is not None:
            models = provider.models()
            return bool(models) and all(
                self._is_open(f"{name}:{m.id}") for m in models
            )
        return False

    def _record_success(self, name: str) -> None:
        self.health[name] = ProviderHealth()

    def _record_failure(self, name: str, quota: bool = False) -> None:
        h = self.health.setdefault(name, ProviderHealth())
        h.failures += 1
        if quota:
            # 429 / quota: skip this model right away for a short time so the
            # next request starts on a model that still has headroom.
            h.open_until = max(
                h.open_until, self.clock() + min(self.cooldown_seconds, QUOTA_COOLDOWN)
            )
            h.failures = max(h.failures, self.failure_threshold)
        elif h.failures >= self.failure_threshold:
            h.open_until = self.clock() + self.cooldown_seconds

    # ---- routing --------------------------------------------------------

    def candidates(
        self,
        capability: str,
        preferred_model: str | None = None,
    ) -> list[tuple[BaseProvider, ModelInfo]]:
        """Ordered (provider, model) candidates able to serve `capability`."""
        chosen: list[tuple[BaseProvider, ModelInfo]] = []
        for provider in self.providers.values():
            if not provider.is_configured():
                continue
            for m in provider.models():
                if capability in m.capabilities and not self._is_open(self._key(provider, m)):
                    chosen.append((provider, m))
        # Free models first; then by provider registration order.
        chosen.sort(key=lambda pm: (not pm[1].free,))
        if preferred_model:
            chosen.sort(key=lambda pm: (pm[1].id != preferred_model,))
        return chosen

    async def complete(
        self,
        messages: list[Message],
        capability: str = "chat",
        model: str | None = None,
        **kw: Any,
    ) -> tuple[str, str, str]:
        """Returns (text, provider_name, model_id). Falls back across providers."""
        attempts: list[tuple[str, str]] = []
        cands = self.candidates(capability, model)
        for provider, m in cands:
            try:
                attempt = 0
                while True:
                    try:
                        text = await provider.chat(messages, model=m.id, **kw)
                        break
                    except ProviderError as exc:
                        # Transient overload (5xx): retry the same model with
                        # jittered backoff. A 429 is a quota signal for this
                        # model: retrying it only burns quota, so move on to
                        # the next model right away.
                        if exc.retryable and exc.status != 429 and attempt < 2:
                            attempt += 1
                            await asyncio.sleep(_backoff(attempt))
                            continue
                        raise
                self._record_success(self._key(provider, m))
                return text, provider.name, m.id
            except ProviderError as exc:
                attempts.append((f"{provider.name}:{m.id}", str(exc)[:120]))
                self._record_failure(self._key(provider, m), quota=exc.status == 429)
                if model and m.id == model:
                    # Manual selection that failed: stop, report honestly.
                    break
                continue
        raise AllProvidersDownError(attempts or [("router", "no provider configured")])

    async def stream(
        self,
        messages: list[Message],
        capability: str = "stream",
        model: str | None = None,
        **kw: Any,
    ) -> AsyncIterator[dict[str, str]]:
        """Yields {"type": "meta"|"token"|"error", ...} events. Falls back to
        the next provider when one fails before producing any token."""
        cands = self.candidates(capability, model)
        attempts: list[tuple[str, str]] = []
        if not cands:
            yield {
                "type": "error",
                "error": "no provider configured for streaming",
            }
            return
        for provider, m in cands:
            produced = False
            attempt = 0
            try:
                while True:
                    try:
                        yield {
                            "type": "meta",
                            "provider": provider.name,
                            "model": m.id,
                        }
                        tokens = provider.chat_stream(
                            messages, model=m.id, **kw
                        ).__aiter__()
                        while True:
                            try:
                                token = await asyncio.wait_for(
                                    tokens.__anext__(),
                                    timeout=_FIRST_TOKEN_TIMEOUT
                                    if not produced
                                    else _NEXT_TOKEN_TIMEOUT,
                                )
                            except StopAsyncIteration:
                                break
                            except asyncio.TimeoutError:
                                if produced:
                                    # Mid-stream stall: keep the partial reply.
                                    break
                                raise ProviderError(
                                    "provider stalled before first token",
                                    provider.name,
                                    retryable=True,
                                )
                            produced = True
                            yield {"type": "token", "content": token}
                        if not produced:
                            raise ProviderError(
                                "provider returned an empty stream",
                                provider.name,
                                retryable=True,
                            )
                        break
                    except ProviderError as exc:
                        # Retry transient pre-token failures once or twice.
                        if (
                            exc.retryable
                            and exc.status != 429
                            and not produced
                            and attempt < 2
                        ):
                            attempt += 1
                            await asyncio.sleep(_backoff(attempt))
                            continue
                        raise
                self._record_success(self._key(provider, m))
                yield {"type": "done", "provider": provider.name, "model": m.id}
                return
            except ProviderError as exc:
                attempts.append((f"{provider.name}:{m.id}", str(exc)[:120]))
                self._record_failure(self._key(provider, m), quota=exc.status == 429)
                if produced or (model and m.id == model):
                    # Stream already started (or manual choice): cannot
                    # silently switch mid-answer.
                    yield {
                        "type": "error",
                        "error": f"{provider.name}: {exc}",
                    }
                    return
                yield {
                    "type": "fallback",
                    "from": provider.name,
                    "reason": str(exc)[:120],
                }
                continue
        yield {
            "type": "error",
            "error": "; ".join(f"{p}: {e}" for p, e in attempts)
            or "all providers failed",
        }

    async def generate_image(self, prompt: str, **kw: Any) -> tuple[str, str, str]:
        """Returns (url, provider, model)."""
        attempts: list[tuple[str, str]] = []
        for provider, m in self.candidates("image_gen"):
            try:
                url = await provider.generate_image(prompt, **kw)
                self._record_success(self._key(provider, m))
                return url, provider.name, m.id
            except ProviderError as exc:
                attempts.append((f"{provider.name}:{m.id}", str(exc)[:120]))
                self._record_failure(self._key(provider, m), quota=exc.status == 429)
                continue
        raise AllProvidersDownError(attempts or [("router", "no image provider")])
