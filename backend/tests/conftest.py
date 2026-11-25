import asyncio
import os
import sys

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import Settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.providers.base import (  # noqa: E402
    CHAT,
    STREAM,
    BaseProvider,
    ModelInfo,
    ProviderError,
)
from app.providers.router import ProviderRouter  # noqa: E402
from app.rate_limit import RateLimiter  # noqa: E402


class FakeProvider(BaseProvider):
    """Scriptable fake: canned replies, programmable failures and stream."""

    def __init__(self, name="fake", reply="أهلاً! أنا شات لاك.", fail_times=0,
                 capabilities=(CHAT, STREAM), model_id="fake-1", configured=True):
        self.name = name
        self.reply = reply
        self.fail_times = fail_times
        self.calls = 0
        self._model = ModelInfo(
            id=model_id, provider=name, capabilities=frozenset(capabilities)
        )
        self._configured = configured

    def models(self):
        return [self._model]

    def is_configured(self):
        return self._configured

    def _maybe_fail(self):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ProviderError(f"{self.name} scripted failure", self.name)

    async def chat(self, messages, model=None, **kw):
        self._maybe_fail()
        return self.reply

    async def chat_stream(self, messages, model=None, **kw):
        self._maybe_fail()
        for token in ["أهلاً", "! ", "أنا شات لاك."]:
            yield token


@pytest.fixture()
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        jwt_secret="test-secret",
        rate_limit_per_minute=1000,
    )


def make_router(*providers, failure_threshold=3, cooldown=120.0):
    router = ProviderRouter(
        failure_threshold=failure_threshold, cooldown_seconds=cooldown
    )
    for p in providers:
        router.register(p)
    return router


@pytest_asyncio.fixture()
async def client(settings):
    router = make_router(FakeProvider())
    app = create_app(settings, provider_router=router,
                     rate_limiter=RateLimiter(1000))
    await init_db(app.state.engine)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.app = app
        yield c


async def register_user(client, email="hossam@example.com", password="secret123"):
    resp = await client.post(
        "/v1/auth/register",
        json={"email": email, "password": password, "display_name": "Hossam"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}
