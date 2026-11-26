"""Chat Luck AI backend — FastAPI application factory."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import AuthDeps
from .config import Settings
from .db import init_db, make_engine, make_session_factory
from .providers import build_default_router
from .providers.router import ProviderRouter
from .rate_limit import RateLimiter


def create_app(
    settings: Settings | None = None,
    provider_router: ProviderRouter | None = None,
    rate_limiter: RateLimiter | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await init_db(engine)
        yield

    app = FastAPI(title="Chat Luck AI", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.auth_deps = AuthDeps(settings, session_factory)
    app.state.provider_router = provider_router or build_default_router(settings)
    app.state.rate_limiter = rate_limiter or RateLimiter(settings.rate_limit_per_minute)

    from .api_auth import router as auth_router
    from .api_chat import router as chat_router
    from .api_files import router as files_router
    from .api_agent import router as agent_router
    from .api_files import router as files_router
    from .api_research import router as research_router
    from .api_admin import router as admin_router

    app.include_router(auth_router)
    app.include_router(chat_router)
    app.include_router(files_router)
    app.include_router(research_router)
    app.include_router(agent_router)
    app.include_router(admin_router)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "environment": settings.environment}

    return app


app = create_app()
