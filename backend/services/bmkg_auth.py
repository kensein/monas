"""BMKG API authentication with automatic token refresh (48h expiry)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

from backend.config import BMKG_API_BASE, BMKG_PASSWORD, BMKG_USERNAME, CACHE_DIR

TOKEN_FILE = CACHE_DIR / "bmkg_token.json"
TOKEN_TTL_SECONDS = 47 * 3600  # refresh 1 hour before 48h expiry


class BMKGAuthError(Exception):
    pass


def _load_cached_token() -> dict[str, Any] | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _save_token(token: str, expires_at: float) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({
        "token": token,
        "expires_at": expires_at,
        "username": BMKG_USERNAME,
        "updated_at": time.time(),
    }))


def _extract_token(data: dict[str, Any]) -> str | None:
    if not isinstance(data, dict):
        return None
    for key in ("token", "access_token", "session_token", "jwt"):
        if data.get(key):
            return str(data[key])
    for nest in ("data", "result", "session"):
        nested = data.get(nest)
        if isinstance(nested, dict):
            t = _extract_token(nested)
            if t:
                return t
    return None


async def login(force: bool = False) -> str:
    """Login to BMKG API v21 and cache token."""
    if not BMKG_USERNAME or not BMKG_PASSWORD:
        raise BMKGAuthError(
            "BMKG_USERNAME / BMKG_PASSWORD belum diset. "
            "Buat file .env atau set environment variable."
        )

    if not force:
        cached = _load_cached_token()
        if cached and cached.get("token") and time.time() < cached.get("expires_at", 0):
            return cached["token"]

    url = f"{BMKG_API_BASE.rstrip('/')}/api/v21/user/session/login"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "NWP-Verification-Dashboard/1.0",
    }

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.post(
            url,
            json={"username": BMKG_USERNAME, "password": BMKG_PASSWORD},
            headers=headers,
        )

        if resp.status_code == 403 and "cloudflare" in resp.text.lower():
            raise BMKGAuthError(
                "BMKG API diblokir dari jaringan ini (Cloudflare). "
                "Jalankan dashboard di komputer/jaringan BMKG yang bisa akses bmkgsatu.bmkg.go.id."
            )

        if resp.status_code >= 400:
            raise BMKGAuthError(f"Login gagal HTTP {resp.status_code}: {resp.text[:300]}")

        try:
            data = resp.json()
        except Exception as exc:
            raise BMKGAuthError(f"Response login bukan JSON: {resp.text[:200]}") from exc

        token = _extract_token(data)
        if not token:
            raise BMKGAuthError(f"Token tidak ditemukan. Keys: {list(data.keys())}")

        expires_at = time.time() + TOKEN_TTL_SECONDS
        _save_token(token, expires_at)
        return token


async def get_token(force_refresh: bool = False) -> str:
    """Return valid token, auto-refresh if expired."""
    return await login(force=force_refresh)


def token_status() -> dict[str, Any]:
    cached = _load_cached_token()
    if not cached:
        return {"logged_in": False, "message": "Belum login"}
    remaining = cached.get("expires_at", 0) - time.time()
    return {
        "logged_in": remaining > 0,
        "username": cached.get("username"),
        "expires_in_hours": round(max(0, remaining) / 3600, 1),
        "expires_at": cached.get("expires_at"),
        "auto_refresh": True,
    }
