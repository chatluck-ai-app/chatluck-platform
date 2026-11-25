"""Model router: capability-aware selection, automatic fallback, and a
circuit breaker per provider. Manual selection is honored when possible;
automatic routing prefers free, healthy providers."""
from __future__ import annotations

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
                        and not self._is_open(provider.name),
                    }
                )
        return out

    def _is_open(self, name: str) -> bool:
        h = self.health[name]
        return h.failures >= self.failure_threshold and self.clock() < h.open_until

    def _record_success(self, name: str) -> None:
        self.health[name] = ProviderHealth()

    def _record_failure(self, name: str) -> None:
        h = self.health[name]
        h.failures += 1
        if h.failures >= self.failure_threshold:
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
            if not provider.is_configured() or self._is_open(provider.name):
                continue
            for m in provider.models():
                if capability in m.capabilities:
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
                text = await provider.chat(messages, model=m.id, **kw)
                self._record_success(provider.name)
                return text, provider.name, m.id
            except ProviderError as exc:
                attempts.append((provider.name, str(exc)[:120]))
                if exc.retryable:
                    self._record_failure(provider.name)
                else:
                    self._record_failure(provider.name)
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
            try:
                yield {
                    "type": "meta",
                    "provider": provider.name,
                    "model": m.id,
                }
                async for token in provider.chat_stream(messages, model=m.id, **kw):
                    produced = True
                    yield {"type": "token", "content": token}
                self._record_success(provider.name)
                yield {"type": "done", "provider": provider.name, "model": m.id}
                return
            except ProviderError as exc:
                attempts.append((provider.name, str(exc)[:120]))
                self._record_failure(provider.name)
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
                self._record_success(provider.name)
                return url, provider.name, m.id
            except ProviderError as exc:
                attempts.append((provider.name, str(exc)[:120]))
                self._record_failure(provider.name)
                continue
        raise AllProvidersDownError(attempts or [("router", "no image provider")])
