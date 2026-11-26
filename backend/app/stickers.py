"""Sticker packs manifest for the Chat Luck keyboard.

GET /v1/stickers/manifest.json returns the curated pack list. Twemoji
assets are absolute jsDelivr CDN URLs; Chat Luck's own Arabic packs are
served from /v1/stickers/files and expanded with the request base URL so
the same manifest works locally and in production.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Request

router = APIRouter(prefix="/v1", tags=["stickers"])

_STATIC_DIR = Path(__file__).parent / "static" / "stickers"


@lru_cache(maxsize=1)
def _load_manifest() -> dict:
    return json.loads((_STATIC_DIR / "manifest.base.json").read_text(encoding="utf-8"))


@router.get("/stickers/manifest.json")
async def stickers_manifest(request: Request) -> dict:
    base = str(request.base_url).rstrip("/")
    data = _load_manifest()
    packs = []
    for pack in data["packs"]:
        p = dict(pack)
        p["files"] = [u.replace("{BASE}", base) if u.startswith("{BASE}") else u for u in p["files"]]
        p["count"] = len(p["files"])
        packs.append(p)
    return {"packs": packs}
