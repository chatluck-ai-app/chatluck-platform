"""Pollinations image provider: free, key-less image generation."""
from __future__ import annotations

import urllib.parse

from .base import IMAGE_GEN, BaseProvider, ModelInfo


class PollinationsProvider(BaseProvider):
    name = "pollinations"

    def models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id="pollinations-flux",
                provider=self.name,
                capabilities=frozenset({IMAGE_GEN}),
            )
        ]

    def is_configured(self) -> bool:
        return True  # no key needed

    async def chat(self, messages, model=None, **kw):  # pragma: no cover
        from .base import ProviderError

        raise ProviderError("pollinations does not chat", self.name, False)

    def chat_stream(self, messages, model=None, **kw):  # pragma: no cover
        from .base import ProviderError

        raise ProviderError("pollinations does not chat", self.name, False)

    async def generate_image(self, prompt: str, **kw) -> str:
        encoded = urllib.parse.quote(prompt, safe="")
        width = int(kw.get("width", 1024))
        height = int(kw.get("height", 1024))
        seed = int(kw.get("seed", 42))
        return (
            f"https://image.pollinations.ai/prompt/{encoded}"
            f"?width={width}&height={height}&seed={seed}&nologo=true"
        )
