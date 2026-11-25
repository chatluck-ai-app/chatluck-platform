import base64
import io

import pytest
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

from app.providers.base import CHAT, STREAM, VISION
from app.providers.gemini import GeminiProvider
from conftest import FakeProvider, auth_headers, make_router, register_user

pytestmark = pytest.mark.asyncio


def make_pdf(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    c.drawString(72, 720, text)
    c.save()
    return buf.getvalue()


def test_gemini_payload_includes_inline_image():
    provider = GeminiProvider(api_key="k")
    payload = provider._payload(
        [
            {
                "role": "user",
                "content": "ما هذا؟",
                "attachments": [
                    {"mime_type": "image/png", "data_base64": "aGVsbG8="}
                ],
            }
        ]
    )
    parts = payload["contents"][0]["parts"]
    assert parts[0] == {"inline_data": {"mime_type": "image/png", "data": "aGVsbG8="}}
    assert parts[1] == {"text": "ما هذا؟"}


async def test_chat_with_image_routes_to_vision_provider(client):
    blind = FakeProvider(name="blind", capabilities=(CHAT, STREAM))
    seer = FakeProvider(name="seer", capabilities=(CHAT, STREAM, VISION))
    client.app.state.provider_router = make_router(blind, seer)
    token = await register_user(client, "vision@example.com")
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
    resp = await client.post(
        "/v1/chat",
        json={
            "message": "صف الصورة",
            "attachments": [{"mime_type": "image/png", "data_base64": png}],
        },
        headers=auth_headers(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["provider"] == "seer"


async def test_chat_rejects_non_image_attachment(client):
    token = await register_user(client, "badatt@example.com")
    resp = await client.post(
        "/v1/chat",
        json={
            "message": "x",
            "attachments": [{"mime_type": "application/pdf", "data_base64": "AAAA"}],
        },
        headers=auth_headers(token),
    )
    assert resp.status_code == 422


async def test_pdf_extraction(client):
    token = await register_user(client, "pdf@example.com")
    pdf = make_pdf("Chat Luck AI phase five test document")
    resp = await client.post(
        "/v1/files/extract",
        files={"file": ("test.pdf", pdf, "application/pdf")},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["type"] == "pdf"
    assert data["pages"] == 1
    assert "phase five test" in data["text"]


async def test_text_extraction(client):
    token = await register_user(client, "txt@example.com")
    resp = await client.post(
        "/v1/files/extract",
        files={"file": ("notes.txt", "نص عربي للاختبار".encode(), "text/plain")},
        headers=auth_headers(token),
    )
    assert resp.status_code == 200
    assert "نص عربي" in resp.json()["text"]


async def test_extract_rejects_unknown_type(client):
    token = await register_user(client, "bin@example.com")
    resp = await client.post(
        "/v1/files/extract",
        files={"file": ("x.bin", b"\x00\x01\x02", "application/octet-stream")},
        headers=auth_headers(token),
    )
    assert resp.status_code == 415
