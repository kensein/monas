"""QC observasi BMKG / synop — sentinel missing codes.

Kode 8888 / 9999 (dan nilai |x| ≥ 8888) dipakai BMKG/synop sebagai missing.
QC |error| > 4σ tidak cukup: 8888 bisa lolos jika banyak outlier serupa,
atau tetap tampil di peta sebelum agregasi skor.

Rekan HARP (AccPcp) sering: ``df[df['PRA_PT3H_ACC'] >= 8888] = 0``.
Untuk verifikasi multi-parameter kita **buang** (NaN), bukan set 0 —
set 0 pada hujan menciptakan kasus "hujan nol" palsu.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

# Ambang: 8888, 9999, 99999, -9999, …
OBS_SENTINEL_ABS_MIN = 8888.0


def is_obs_sentinel(value: Any) -> bool:
    if value is None:
        return True
    try:
        v = float(value)
    except (TypeError, ValueError):
        return True
    if math.isnan(v) or math.isinf(v):
        return True
    return abs(v) >= OBS_SENTINEL_ABS_MIN


def sanitize_obs_value(value: Any) -> float:
    """Nilai numerik valid, atau NaN jika sentinel/missing."""
    if is_obs_sentinel(value):
        return float("nan")
    return float(value)


def mask_obs_sentinels(arr: np.ndarray) -> np.ndarray:
    """Salin array float; ganti sentinel → NaN (in-place aman pada salinan)."""
    out = np.asarray(arr, dtype=np.float64).copy()
    if out.size == 0:
        return out
    bad = np.isfinite(out) & (np.abs(out) >= OBS_SENTINEL_ABS_MIN)
    out[bad] = np.nan
    return out


def drop_obs_sentinels(df: pd.DataFrame, col: str = "obs") -> pd.DataFrame:
    """Buang baris dengan nilai obs sentinel."""
    if df.empty or col not in df.columns:
        return df
    vals = pd.to_numeric(df[col], errors="coerce")
    ok = vals.notna() & (vals.abs() < OBS_SENTINEL_ABS_MIN)
    return df.loc[ok].copy()
