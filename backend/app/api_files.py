"""File endpoints: extract text from uploads (PDF, DOCX, XLSX, CSV, plain
text) for document understanding. Extraction is local — no external calls.
DOCX/XLSX parsing uses only the standard library (zip + XML)."""
from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pypdf import PdfReader

from .deps import audit, get_current_user, get_session
from .models import User

router = APIRouter(prefix="/v1/files", tags=["files"])

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
TEXT_TYPES = {"text/plain", "text/markdown", "text/csv", "application/json"}
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def extract_docx(data: bytes) -> str:
    """Pull paragraph text from a .docx (zip of WordprocessingML)."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    paragraphs: list[str] = []
    for para in root.iter(f"{_W}p"):
        runs = [node.text or "" for node in para.iter(f"{_W}t")]
        line = "".join(runs).strip()
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def extract_xlsx(data: bytes) -> str:
    """Render .xlsx sheets as CSV-ish lines: shared strings + cell values."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            ss_root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in ss_root.iter(f"{_S}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{_S}t")))
        sheet_names = sorted(
            n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)
        )
        out: list[str] = []
        for idx, name in enumerate(sheet_names, 1):
            if len(sheet_names) > 1:
                out.append(f"--- ورقة {idx} ---")
            root = ET.fromstring(z.read(name))
            for row in root.iter(f"{_S}row"):
                cells: list[str] = []
                for c in row.iter(f"{_S}c"):
                    v = c.find(f"{_S}v")
                    value = v.text if v is not None and v.text else ""
                    if c.get("t") == "s" and value.isdigit():
                        value = shared[int(value)] if int(value) < len(shared) else value
                    cells.append(value)
                line = " | ".join(cells).strip(" |")
                if line:
                    out.append(line)
    return "\n".join(out)


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
    elif mime == DOCX_MIME or (file.filename or "").lower().endswith(".docx"):
        try:
            text = extract_docx(data)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"unreadable DOCX: {exc}")
        result = {
            "type": "docx",
            "pages": None,
            "characters": len(text),
            "text": text[:100_000],
            "truncated": len(text) > 100_000,
        }
    elif mime == XLSX_MIME or (file.filename or "").lower().endswith(".xlsx"):
        try:
            text = extract_xlsx(data)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"unreadable XLSX: {exc}")
        result = {
            "type": "xlsx",
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
