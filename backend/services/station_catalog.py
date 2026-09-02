"""Katalog metadata stasiun BMKG (WMO ID, nama, lat/lon) dari Matriks UPT."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from backend.config import BASE_DIR

DEFAULT_CATALOG_PATH = BASE_DIR / "backend" / "resources" / "stations_bmkg.json"

_sync_lock = threading.Lock()
_catalog_db_synced = False


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


def _core_station_name(name: str) -> str:
    """Nama inti stasiun tanpa prefix tipe UPT."""
    s = _normalize_name(name)
    prefixes = (
        "stasiun meteorologi kelas i ",
        "stasiun meteorologi kelas ii ",
        "stasiun meteorologi kelas iii ",
        "stasiun meteorologi kelas iv ",
        "stasiun meteorologi ",
        "stasiun klimatologi ",
        "stasiun geofisika ",
        "pos pengamatan meteorologi ",
        "pos meteorologi ",
    )
    changed = True
    while changed:
        changed = False
        for p in prefixes:
            if s.startswith(p):
                s = s[len(p):].strip()
                changed = True
    return s


@lru_cache(maxsize=1)
def _core_to_wmo_map() -> dict[str, str]:
    out: dict[str, str] = {}
    for rec in load_station_catalog():
        wmo = str(rec.get("wmo_id") or "").strip()
        name = str(rec.get("name") or "").strip()
        if not wmo or not name:
            continue
        core = _core_station_name(name)
        if core and core not in out:
            out[core] = wmo
    return out


def lookup_wmo_by_name(station_name: str) -> str | None:
    """Resolve WMO ID dari nama stasiun (exact + fuzzy core name)."""
    key = _normalize_name(station_name)
    if not key:
        return None
    hit = _name_to_wmo_map().get(key)
    if hit:
        return hit
    core = _core_station_name(station_name)
    if core:
        hit = _core_to_wmo_map().get(core)
        if hit:
            return hit
        if len(core) >= 4:
            for cat_core, wmo in _core_to_wmo_map().items():
                if core in cat_core or cat_core in core:
                    return wmo
    return None


def catalog_coverage_report(obs_station_names: list[str]) -> dict[str, Any]:
    """Laporan stasiun obs yang tidak match katalog."""
    matched, unmatched = [], []
    for name in obs_station_names:
        if lookup_wmo_by_name(name):
            matched.append(name)
        else:
            unmatched.append(name)
    return {
        "total": len(obs_station_names),
        "matched": len(matched),
        "unmatched": len(unmatched),
        "unmatched_names": sorted(set(unmatched))[:50],
    }


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


def sync_catalog_to_db(force: bool = False) -> int:
    """Upsert semua stasiun katalog ke SQLite (sekali saat startup / import)."""
    global _catalog_db_synced
    if _catalog_db_synced and not force:
        return 0

    with _sync_lock:
        if _catalog_db_synced and not force:
            return 0

        from backend.services.obs_fetcher import get_db

        df = catalog_to_dataframe()
        if df.empty:
            return 0

        rows = [
            (row["station_id"], row["wmo_id"], row["name"], row["lat"], row["lon"], row["region"])
            for _, row in df.iterrows()
        ]

        last_err: Exception | None = None
        for attempt in range(5):
            try:
                conn = get_db()
                conn.executemany(
                    """INSERT OR REPLACE INTO stations (station_id, wmo_id, name, lat, lon, region)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    rows,
                )
                conn.commit()
                conn.close()
                _catalog_db_synced = True
                return len(rows)
            except sqlite3.OperationalError as e:
                last_err = e
                if "locked" not in str(e).lower():
                    raise
                time.sleep(0.2 * (attempt + 1))

        if last_err:
            raise last_err
        return 0


def invalidate_catalog_cache() -> None:
    """Reset cache setelah update file katalog."""
    global _catalog_db_synced
    load_station_catalog.cache_clear()
    _name_to_wmo_map.cache_clear()
    _core_to_wmo_map.cache_clear()
    _catalog_db_synced = False
