import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.api_chat as api_chat
from app.db import init_db
from app.main import create_app
from app.providers.base import IMAGE_GEN, ModelInfo
from app.rate_limit import RateLimiter

from conftest import FakeProvider, auth_headers, make_router, register_user


class FakeImageProvider(FakeProvider):
    def __init__(self, name="imgfake", url="https://img.example/x.png"):
        super().__init__(name=name, capabilities=frozenset({IMAGE_GEN}))
        self._url = url

    def models(self):
        return [ModelInfo(id="img-1", provider=self.name, capabilities=frozenset({IMAGE_GEN}))]

    async def generate_image(self, prompt, **kw):
        return self._url


@pytest_asyncio.fixture()
async def img_client(settings, monkeypatch):
    router = make_router(FakeProvider(), FakeImageProvider())
    app = create_app(settings, provider_router=router, rate_limiter=RateLimiter(1000))
    await init_db(app.state.engine)
    monkeypatch.setattr(api_chat, "_IMG_RETRY_SLEEP", 0)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def test_image_dims_png():
    # minimal PNG header: signature + IHDR with 1280x720
    import struct

    data = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1280, 720)
    assert api_chat._image_dims(data) == (1280, 720)


def test_image_dims_jpeg_and_garbage():
    import struct

    # SOI + SOF0 segment with 800x600
    seg = b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", 600, 800) + b"\x03" + b"\x00" * 6
    assert api_chat._image_dims(b"\xff\xd8" + seg) == (800, 600)
    assert api_chat._image_dims(b"not an image") is None


@pytest.mark.asyncio
async def test_generation_succeeds_when_dims_match(img_client, monkeypatch):
    monkeypatch.setattr(api_chat, "_fetch_image_dims", lambda url: _async((1024, 1024)))
    token = await register_user(img_client)
    resp = await img_client.post("/v1/images", json={"prompt": "قطة"}, headers=auth_headers(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["url"] == "https://img.example/x.png"


@pytest.mark.asyncio
async def test_generation_retries_then_succeeds(img_client, monkeypatch):
    calls = {"n": 0}

    async def fake_dims(url):
        calls["n"] += 1
        return (1024, 576) if calls["n"] < 3 else (1024, 1024)

    monkeypatch.setattr(api_chat, "_fetch_image_dims", fake_dims)
    token = await register_user(img_client)
    resp = await img_client.post("/v1/images", json={"prompt": "قطة"}, headers=auth_headers(token))
    assert resp.status_code == 200, resp.text
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_generation_503_when_stuck_busy(img_client, monkeypatch):
    monkeypatch.setattr(api_chat, "_fetch_image_dims", lambda url: _async((1024, 576)))
    token = await register_user(img_client)
    resp = await img_client.post("/v1/images", json={"prompt": "قطة"}, headers=auth_headers(token))
    assert resp.status_code == 503
    assert "مشغولة" in resp.json()["detail"]


def _async(value):
    class _R:
        def __await__(self):
            yield
            return value

    return _R()
