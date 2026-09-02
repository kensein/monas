"""Normalisasi format observasi Sinoptik BMKG (dict, list-of-list, nested)."""
from __future__ import annotations

from typing import Any


def flatten_sinoptik_records(raw: Any) -> list[dict[str, Any]]:
    """
    Normalisasi berbagai format response BMKG export ke list of dict.
    BMKG API kadang mengembalikan list of list (header + rows) atau nested dict.
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

    # [[{...}, {...}, ...]] — satu wrapper
    if len(raw) == 1 and isinstance(raw[0], list) and raw[0] and isinstance(raw[0], dict):
        return flatten_sinoptik_records(raw[0])

    # Format: [["col1","col2",...], [val1,val2,...], ...]
    if isinstance(raw[0], list) and raw[0] and isinstance(raw[0][0], str):
        if all(isinstance(h, str) for h in raw[0]):
            headers = [str(h) for h in raw[0]]
            rows: list[dict[str, Any]] = []
            for row in raw[1:]:
                if not isinstance(row, list) or len(row) != len(headers):
                    continue
                rows.append(dict(zip(headers, row)))
            if rows:
                return rows

    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            out.extend(_expand_nested_obs_dict(item))
        elif isinstance(item, list):
            # [station_dict, [obs_dict, ...]]
            if len(item) >= 2 and isinstance(item[0], dict) and isinstance(item[1], list):
                base = item[0]
                for obs in item[1]:
                    if isinstance(obs, dict):
                        out.append({**base, **obs})
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
    return [d]
