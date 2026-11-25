"""Provider gateway contracts. Every AI provider implements BaseProvider so
models can be swapped without touching application code."""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, AsyncIterator

# A message is role + text content, optionally with binary parts
# (images etc.) as {"mime_type": ..., "data_base64": ...}.
Message = dict[str, Any]


@dataclass(frozen=True)
class Attachment:
    mime_type: str
    data_base64: str

# Capabilities used across the platform
CHAT = "chat"
STREAM = "stream"
VISION = "vision"
TOOLS = "tools"
IMAGE_GEN = "image_gen"
STT = "stt"
TTS = "tts"
VIDEO_GEN = "video_gen"


@dataclass(frozen=True)
class ModelInfo:
    id: str
    provider: str
    capabilities: frozenset[str]
    context_window: int = 8192
    free: bool = True


class ProviderError(Exception):
    """A provider call failed. retryable=False means do not try the same
    provider again for this request (e.g. invalid key)."""

    def __init__(
        self,
        message: str,
        provider: str,
        retryable: bool = True,
        status: int | None = None,
    ):
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable
        self.status = status


class BaseProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    def models(self) -> list[ModelInfo]:
        ...

    @abc.abstractmethod
    def is_configured(self) -> bool:
        """True when the credentials/settings this provider needs are present."""

    @abc.abstractmethod
    async def chat(
        self, messages: list[Message], model: str | None = None, **kw: Any
    ) -> str:
        ...

    @abc.abstractmethod
    def chat_stream(
        self, messages: list[Message], model: str | None = None, **kw: Any
    ) -> AsyncIterator[str]:
        ...

    async def generate_image(self, prompt: str, **kw: Any) -> str:
        raise ProviderError("image generation not supported", self.name, False)

    def capabilities(self) -> set[str]:
        caps: set[str] = set()
        for m in self.models():
            caps |= set(m.capabilities)
        return caps
