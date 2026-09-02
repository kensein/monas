"""Katalog metadata stasiun BMKG (WMO ID, nama, lat/lon) dari Matriks UPT."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from backend.config import BASE_DIR, DATA_DIR

DEFAULT_CATALOG_PATH = BASE_DIR / "backend" / "resources" / "stations_bmkg.json"


def _normalize_name(name: str) -> str:
    s = re.sub(r"\s+", " ", str(name or "").strip().lower())
    # Singkatan umum di nama stasiun
    s = s.replace("staklim", "stasiun klimatologi")
    return s


@lru_cache(maxsize=1)
def load_station_catalog(path: str | Path | None = None) -> list[dict[str, Any]]:
    p = Path(path) if path else DEFAULT_CATALOG_PATH
    env = __import__("backend.config", fromlist=["STATION_CATALOG_PATH"]).STATION_CATALOG_PATH
    if env:
        p = Path(env)
    if not p.is_file():
        alt = BASE_DIR / "backend" / "resources" / "stations_bmkg.json"
        p = alt if alt.is_file() else p
    if not p.is_file():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


@lru_cache(maxsize=1)
def _name_to_wmo_map() -> dict[str, str]:
    out: dict[str, str] = {}
    for rec in load_station_catalog():
        wmo = str(rec.get("wmo_id") or "").strip()
        name = str(rec.get("name") or "").strip()
        if not wmo or not name:
            continue
        out[_normalize_name(name)] = wmo
    return out


def lookup_wmo_by_name(station_name: str) -> str | None:
    """Resolve WMO ID dari nama stasiun (exact, case-insensitive)."""
    key = _normalize_name(station_name)
    if not key:
        return None
    hit = _name_to_wmo_map().get(key)
    if hit:
        return hit
    # Coba tanpa prefix redundan
    for prefix in ("stasiun meteorologi ", "stasiun klimatologi ", "stasiun geofisika "):
        if key.startswith(prefix):
            short = key[len(prefix):]
            for full, wmo in _name_to_wmo_map().items():
                if full.endswith(short) or short in full:
                    return wmo
    return None


def catalog_to_dataframe() -> pd.DataFrame:
    rows = []
    for rec in load_station_catalog():
        wmo = str(rec.get("wmo_id") or "").strip()
        if not wmo:
            continue
        rows.append({
            "station_id": wmo,
            "wmo_id": wmo,
            "name": rec.get("name") or wmo,
            "lat": rec.get("lat"),
            "lon": rec.get("lon"),
            "region": rec.get("region") or "",
            "elevation": rec.get("elevation"),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.drop_duplicates("station_id").reset_index(drop=True)


def sync_catalog_to_db() -> int:
    """Upsert semua stasiun katalog ke SQLite."""
    from backend.services.obs_fetcher import get_db

    df = catalog_to_dataframe()
    if df.empty:
        return 0
    conn = get_db()
    n = 0
    for _, row in df.iterrows():
        conn.execute(
            """INSERT OR REPLACE INTO stations (station_id, wmo_id, name, lat, lon, region)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (row["station_id"], row["wmo_id"], row["name"], row["lat"], row["lon"], row["region"]),
        )
        n += 1
    conn.commit()
    conn.close()
    return n
