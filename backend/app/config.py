"""Application configuration. All secrets come from the environment; nothing is
hard-coded. See .env.example for the template."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    database_url: str = "sqlite+aiosqlite:///./chatluck.db"
    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60 * 24 * 7

    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    openrouter_api_key: str | None = None

    # Provider behaviour
    provider_timeout_seconds: float = 60.0
    circuit_breaker_failures: int = 3
    circuit_breaker_cooldown_seconds: float = 120.0

    # Rate limiting (per user, in-memory token bucket)
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    rate_limit_per_minute: int = 60

    environment: str = "development"

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        s.database_url = os.environ.get("DATABASE_URL", s.database_url)
        # Render/Heroku style postgres:// -> async driver
        if s.database_url.startswith("postgres://"):
            s.database_url = s.database_url.replace(
                "postgres://", "postgresql+asyncpg://", 1
            )
        elif s.database_url.startswith("postgresql://"):
            s.database_url = s.database_url.replace(
                "postgresql://", "postgresql+asyncpg://", 1
            )
        s.jwt_secret = os.environ.get("JWT_SECRET", s.jwt_secret)
        s.gemini_api_key = os.environ.get("GEMINI_API_KEY") or None
        s.groq_api_key = os.environ.get("GROQ_API_KEY") or None
        s.openrouter_api_key = os.environ.get("OPENROUTER_API_KEY") or None
        s.environment = os.environ.get("ENVIRONMENT", s.environment)
        if s.environment == "production" and s.jwt_secret == cls.jwt_secret:
            raise RuntimeError("JWT_SECRET must be set in production")
        return s


settings = Settings.from_env()
