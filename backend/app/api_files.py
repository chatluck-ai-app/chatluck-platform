"""File endpoints: extract text from uploads (PDF, plain text) for document
understanding. Extraction is local — no external calls."""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pypdf import PdfReader

from .deps import audit, get_current_user, get_session
from .models import User

router = APIRouter(prefix="/v1/files", tags=["files"])

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
TEXT_TYPES = {"text/plain", "text/markdown", "text/csv", "application/json"}


@router.post("/extract")
async def extract_text(
    file: UploadFile,
    user: User = Depends(get_current_user),
    session=Depends(get_session),
):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file too large (15 MB max)")
    mime = file.content_type or ""
    if mime == "application/pdf" or (file.filename or "").lower().endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(data))
            pages = [(page.extract_text() or "") for page in reader.pages]
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"unreadable PDF: {exc}")
        text = "\n\n".join(p for p in pages if p.strip())
        result = {
            "type": "pdf",
            "pages": len(pages),
            "characters": len(text),
            "text": text[:100_000],
            "truncated": len(text) > 100_000,
        }
    elif mime in TEXT_TYPES or mime.startswith("text/"):
        text = data.decode("utf-8", errors="replace")
        result = {
            "type": "text",
            "pages": None,
            "characters": len(text),
            "text": text[:100_000],
            "truncated": len(text) > 100_000,
        }
    else:
        raise HTTPException(
            status_code=415, detail=f"unsupported type: {mime or 'unknown'}"
        )
    await audit(session, user.id, "files.extract", result["type"])
    await session.commit()
    return result
