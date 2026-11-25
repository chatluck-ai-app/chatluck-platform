from .base import (  # noqa: F401
    CHAT,
    IMAGE_GEN,
    STREAM,
    STT,
    TOOLS,
    TTS,
    VIDEO_GEN,
    VISION,
    BaseProvider,
    Message,
    ModelInfo,
    ProviderError,
)
from .gemini import GeminiProvider  # noqa: F401
from .openai_compatible import (  # noqa: F401
    OpenAICompatibleProvider,
    make_groq,
    make_openrouter,
)
from .pollinations import PollinationsProvider  # noqa: F401
from .router import AllProvidersDownError, ProviderRouter  # noqa: F401


def build_default_router(settings) -> ProviderRouter:
    """Assemble the router from configured providers. Only providers whose
    credentials exist are registered; order defines auto-route preference."""
    router = ProviderRouter(
        failure_threshold=settings.circuit_breaker_failures,
        cooldown_seconds=settings.circuit_breaker_cooldown_seconds,
    )
    router.register(GeminiProvider(settings.gemini_api_key))
    router.register(make_groq(settings.groq_api_key))
    router.register(make_openrouter(settings.openrouter_api_key))
    router.register(PollinationsProvider())
    return router
