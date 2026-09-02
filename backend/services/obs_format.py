"""Normalisasi format observasi Sinoptik BMKG (dict, list-of-list, nested)."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from backend.config import SINOPTIK_PARAMETERS

# BMKG export API v21: setiap baris = list 105 kolom, urutan = SINOPTIK_PARAMETERS
BMKG_ROW_COLUMN_COUNT = len(SINOPTIK_PARAMETERS)


def _looks_like_timestamp(val: Any) -> bool:
    if not isinstance(val, str):
        return False
    return bool(re.match(r"\d{4}-\d{2}-\d{2}", val))


def _station_id_from_row(rec: dict[str, Any]) -> str:
    """Resolve station_id — BMKG export baris 105 kolom tidak punya wmo_id terpisah."""
    for key in ("station_wmo_id", "wmo_id", "station_id", "stationWmoId", "wmo"):
        if rec.get(key):
            return str(rec[key])
    name = str(rec.get("station_name") or "").strip()
    if not name:
        return ""
    # Katalog BMKG (Matriks UPT / stations_bmkg.json)
    from backend.services.station_catalog import lookup_wmo_by_name
    wmo = lookup_wmo_by_name(name)
    if wmo:
        return wmo
    # Coba ekstrak WMO 5 digit dari nama
    m = re.search(r"\b(\d{5})\b", name)
    if m:
        return m.group(1)
    # Nama → id stabil (hash) — fallback jika tidak ada di katalog
    return hashlib.md5(name.encode("utf-8")).hexdigest()[:12]


def _row_list_to_dict(row: list[Any]) -> dict[str, Any] | None:
    """Convert satu baris list[105] → dict sesuai urutan SINOPTIK_PARAMETERS."""
    if not isinstance(row, list) or len(row) < 2:
        return None
    n = min(len(row), len(SINOPTIK_PARAMETERS))
    rec = dict(zip(SINOPTIK_PARAMETERS[:n], row[:n]))
    sid = _station_id_from_row(rec)
    if sid:
        rec["station_wmo_id"] = sid
    return rec


def _is_bmkg_parameter_row(row: list[Any]) -> bool:
    """Baris data: [station_name, timestamp, ...] tanpa header."""
    if not isinstance(row, list) or len(row) < 2:
        return False
    if isinstance(row[0], str) and _looks_like_timestamp(row[1]):
        return True
    return len(row) == BMKG_ROW_COLUMN_COUNT


def flatten_sinoptik_records(raw: Any) -> list[dict[str, Any]]:
    """
    Normalisasi berbagai format response BMKG export ke list of dict.
    Format utama BMKG v21: list of list, 105 kolom per baris (SINOPTIK_PARAMETERS).
    """
    if raw is None:
        return []
    if isinstance(raw, dict):
        for key in ("data", "results", "items", "records", "observations", "rows"):
            val = raw.get(key)
            if isinstance(val, list):
                return flatten_sinoptik_records(val)
        return [raw] if raw else []

    if not isinstance(raw, list) or not raw:
        return []

    # [[{...}, ...]] wrapper
    if len(raw) == 1 and isinstance(raw[0], list) and raw[0] and isinstance(raw[0], dict):
        return flatten_sinoptik_records(raw[0])

    # BMKG v21: list of list[105] — station_name, data_timestamp, params...
    if isinstance(raw[0], list) and _is_bmkg_parameter_row(raw[0]):
        rows: list[dict[str, Any]] = []
        for row in raw:
            rec = _row_list_to_dict(row)
            if rec:
                rows.append(rec)
        if rows:
            return rows

    # Format: [["col1","col2",...], [val1,val2,...], ...] dengan header string
    if isinstance(raw[0], list) and raw[0] and isinstance(raw[0][0], str):
        if all(isinstance(h, str) for h in raw[0]) and raw[0][0] in SINOPTIK_PARAMETERS:
            headers = [str(h) for h in raw[0]]
            rows = []
            for row in raw[1:]:
                if isinstance(row, list) and len(row) == len(headers):
                    rows.append(dict(zip(headers, row)))
            if rows:
                return rows

    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            out.extend(_expand_nested_obs_dict(item))
        elif isinstance(item, list):
            if _is_bmkg_parameter_row(item):
                rec = _row_list_to_dict(item)
                if rec:
                    out.append(rec)
                continue
            if len(item) >= 2 and not isinstance(item[0], dict):
                sid = str(item[0])
                if isinstance(item[1], dict):
                    rec = dict(item[1])
                    rec.setdefault("station_wmo_id", sid)
                    out.append(rec)
                    continue
            if item and isinstance(item[0], (dict, list)):
                out.extend(flatten_sinoptik_records(item))
    return out


def _expand_nested_obs_dict(d: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("observations", "records", "data", "readings", "values", "rows"):
        nested = d.get(key)
        if isinstance(nested, list) and nested:
            if isinstance(nested[0], dict):
                base = {k: v for k, v in d.items() if k != key}
                return [{**base, **row} for row in nested]
            if isinstance(nested[0], list):
                return flatten_sinoptik_records(nested)
    sid = _station_id_from_row(d)
    if sid:
        d = {**d, "station_wmo_id": sid}
    return [d]
