"""Dummy forecast + verification for models without NC (InaCAWO, GFS, IFS).

Derived from real InaNWP point forecasts with model-specific perturbation
until real NC files are available.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

import numpy as np
import pandas as pd

from backend.config import DUMMY_MODELS, MAX_LEAD_TIME_HOURS
from backend.services.obs_fetcher import get_db, load_forecasts, save_forecasts
from backend.services.verification_cache import compute_run_verification, refresh_ranking_cache, save_verification_station_scores

# save_verification_scores imported lazily to avoid circular import with pipeline

# Perturbation profile per model (deterministic seed per model+init)
DUMMY_PROFILES: dict[str, dict[str, float]] = {
    "InaCAWO": {"temp_bias": 0.15, "wind_bias": 0.12, "pressure_bias": 0.3, "noise_scale": 0.25},
    "GFS": {"temp_bias": 0.9, "wind_bias": 0.55, "pressure_bias": 1.2, "noise_scale": 0.45},
    "IFS": {"temp_bias": 0.25, "wind_bias": 0.18, "pressure_bias": 0.5, "noise_scale": 0.30},
}


def _perturb_value(val: float, param: str, profile: dict[str, float], rng: np.random.Generator) -> float:
    ns = profile["noise_scale"]
    if param == "temp_drybulb_c_tttttt":
        return val + profile["temp_bias"] + rng.normal(0, ns * 0.4)
    if param == "wind_speed_ff":
        return max(0, val + profile["wind_bias"] + rng.normal(0, ns * 0.3))
    if param == "wind_dir_deg_dd":
        return (val + rng.normal(0, ns * 15)) % 360
    if param.startswith("pressure"):
        return val + profile["pressure_bias"] + rng.normal(0, ns * 0.6)
    if param == "relative_humidity_pc":
        return float(np.clip(val + rng.normal(0, ns * 4), 0, 100))
    if param.startswith("rainfall"):
        return max(0, val + rng.normal(0, ns * 0.5))
    if param == "cloud_cover_oktas_m":
        return float(np.clip(val + rng.normal(0, ns * 0.8), 0, 8))
    return val + rng.normal(0, ns * 0.2)


def generate_dummy_forecasts(source_model: str, init_time: str, target_models: list[str] | None = None) -> dict[str, Any]:
    """Buat forecast dummy dari InaNWP real untuk model tanpa NC."""
    targets = target_models or DUMMY_MODELS
    fcst = load_forecasts(models=[source_model])
    fcst = fcst[(fcst["init_time"] == init_time) & (fcst["lead_time"] <= MAX_LEAD_TIME_HOURS)]
    if fcst.empty:
        return {"error": f"Tidak ada forecast {source_model} untuk init {init_time}", "records": 0}

    rows = []
    for model in targets:
        if model == source_model:
            continue
        profile = DUMMY_PROFILES.get(model, DUMMY_PROFILES["GFS"])
        seed = hash(f"{model}:{init_time}") % (2**31)
        rng = np.random.default_rng(seed)
        for _, row in fcst.iterrows():
            new_row = row.to_dict()
            new_row["model"] = model
            new_row["fcst"] = _perturb_value(float(row["fcst"]), row["parameter"], profile, rng)
            rows.append(new_row)

    df = pd.DataFrame(rows)
    saved = save_forecasts(df) if not df.empty else 0
    return {"records": saved, "models": targets, "source": source_model, "init_time": init_time}


def verify_dummy_models(
    init_time: str,
    source_model: str = "InaNWP",
    target_models: list[str] | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Generate dummy fcst + hitung skor HARP."""
    from backend.services.obs_fetcher import load_observations
    from backend.services.pipeline import save_verification_scores, _ensure_observations

    targets = target_models or DUMMY_MODELS

    gen = generate_dummy_forecasts(source_model, init_time, targets)
    if gen.get("error"):
        return gen

    _ensure_observations(init_time, progress_cb)
    obs_df = load_observations()
    if obs_df.empty:
        return {"error": "Observasi kosong", **gen}

    all_scores: list[dict] = []
    all_station_rows: list[dict] = []
    for model in targets:
        if progress_cb:
            progress_cb(80, f"Verifikasi dummy {model}...")
        scores, station_rows = compute_run_verification(model, init_time, obs_df)
        all_scores.extend(scores)
        all_station_rows.extend(station_rows)

    saved = save_verification_scores(all_scores)
    save_verification_station_scores(all_station_rows)
    refresh_ranking_cache()

    conn = get_db()
    ts = datetime.utcnow().isoformat()
    for model in targets:
        conn.execute(
            """INSERT OR REPLACE INTO model_runs
               (model, init_time, nc_filename, nc_path, file_size, status, processed_at, data_source)
               VALUES (?, ?, ?, ?, ?, 'done', ?, 'dummy')""",
            (model, init_time, f"DUMMY←{source_model}", "", 0, ts),
        )
    conn.commit()
    conn.close()

    return {"scores_saved": saved, **gen}


def get_model_data_sources() -> dict[str, str]:
    """Return model → real|dummy from latest model_runs."""
    conn = get_db()
    rows = conn.execute(
        "SELECT model, data_source FROM model_runs ORDER BY init_time DESC"
    ).fetchall()
    conn.close()
    sources: dict[str, str] = {"InaNWP": "real"}
    for r in rows:
        m = r["model"] if hasattr(r, "keys") else r[0]
        ds = (r["data_source"] if hasattr(r, "keys") else r[1]) or "real"
        if m not in sources:
            sources[m] = ds
    for m in DUMMY_MODELS:
        sources.setdefault(m, "dummy")
    return sources
