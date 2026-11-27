"""Real client IP behind Cloudflare + Render.

Chain: client -> Cloudflare edge -> Render proxy -> Render internal (10.x) -> app.
Every hop appends; a caller can only forge entries on the LEFT. The client is the
entry just left of the rightmost Cloudflare-range entry whose right side is only
Cloudflare/private addresses. Anything else shares one "unknown" bucket (fail-safe:
over-throttles, never over-allows). Same logic as MAHDI v5.4, verified live there."""
from __future__ import annotations

import ipaddress

from fastapi import Request

_CF = [
    ipaddress.ip_network(r)
    for r in [
        "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
        "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
        "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
        "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
        "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
        "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
    ]
]
UNKNOWN = "unknown"


def _is_cf(addr) -> bool:
    return any(addr in n for n in _CF)


def _is_internal(addr) -> bool:
    return addr.is_private or addr.is_loopback or addr.is_link_local


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    entries = []
    for part in fwd.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            entries.append((part, ipaddress.ip_address(part)))
        except ValueError:
            return UNKNOWN
    for i in range(len(entries) - 2, 0, -1):
        if not _is_cf(entries[i][1]):
            continue
        if all(_is_cf(a) or _is_internal(a) for _, a in entries[i + 1:]):
            derived = entries[i - 1][0]
            cf_header = request.headers.get("cf-connecting-ip", "").strip()
            if cf_header and cf_header != derived:
                return UNKNOWN
            return derived
        return UNKNOWN
    return UNKNOWN
