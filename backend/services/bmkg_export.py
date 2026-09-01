"""Fetch Sinoptik observations from BMKG API v21 (POST export)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx

from backend.config import BMKG_API_BASE, SINOPTIK_PARAMETERS
from backend.services.bmkg_auth import BMKGAuthError, get_token, login


async def _request_with_retry(method: str, url: str, **kwargs) -> httpx.Response:
    token = await get_token()
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"
    headers.setdefault("Accept", "application/json")

    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        resp = await client.request(method, url, headers=headers, **kwargs)
        if resp.status_code == 401:
            token = await login(force=True)
            headers["Authorization"] = f"Bearer {token}"
            resp = await client.request(method, url, headers=headers, **kwargs)
        return resp


async def fetch_sinoptik_chunk(
    date_from: str,
    date_to: str,
    station_wmo_ids: list[str] | None = None,
    parameter_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    POST /api/v21/export/observation/by-station/query
    Sesuai API Export Sinoptik (search api).pptx slide 5-6.
    """
    body = {
        "data_type": "sinoptik",
        "parameter_names": parameter_names or ["*"],
        "station_wmo_ids": station_wmo_ids or ["*"],
        "date_from": date_from,
        "date_to": date_to,
        "order_timestamp_code": 1,
    }

    url = f"{BMKG_API_BASE.rstrip('/')}/api/v21/export/observation/by-station/query"
    resp = await _request_with_retry("POST", url, json=body)

    if resp.status_code >= 400:
        raise BMKGAuthError(f"Export sinoptik gagal HTTP {resp.status_code}: {resp.text[:400]}")

    data = resp.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "results", "items", "records", "observations"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _parse_dt(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")


async def fetch_sinoptik_range(
    date_from: str,
    date_to: str,
    max_days_per_request: int = 4,
    **kwargs,
) -> list[dict[str, Any]]:
    """
    Fetch observasi dalam chunk ≤4 hari (rekomendasi PPTX: <5 hari per request).
    """
    start = _parse_dt(date_from)
    end = _parse_dt(date_to)
    all_records: list[dict[str, Any]] = []

    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=max_days_per_request), end)
        chunk_from = cursor.strftime("%Y-%m-%dT%H:%M:%SZ")
        chunk_to = chunk_end.strftime("%Y-%m-%dT%H:%M:%SZ")
        records = await fetch_sinoptik_chunk(chunk_from, chunk_to, **kwargs)
        all_records.extend(records)
        cursor = chunk_end + timedelta(seconds=1)

    return all_records
