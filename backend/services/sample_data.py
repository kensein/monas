"""Generate sample NetCDF + demo data for dashboard testing."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from backend.config import NC_DIR, VERIFY_PARAMETERS
from backend.services.obs_fetcher import get_stations, init_db, save_forecasts, save_observations


def generate_sample_nc(output_path: Path | None = None, init_time: datetime | None = None) -> Path:
    init_time = init_time or datetime(2026, 7, 1, 12, 0, 0)
    output_path = output_path or NC_DIR / "2026070112-d01-asim.nc"

    lats = np.linspace(-11, 6, 40)
    lons = np.linspace(95, 141, 50)
    times = [init_time + timedelta(hours=h) for h in range(0, 49, 3)]
    lon2d, lat2d = np.meshgrid(lons, lats)

    ntime = len(times)
    T2 = 300 + 5 * np.sin(np.radians(lat2d))[None, :, :] + np.random.randn(ntime, *lat2d.shape) * 0.5
    U10 = 3 + np.random.randn(ntime, *lat2d.shape) * 0.3
    V10 = 2 + np.random.randn(ntime, *lat2d.shape) * 0.3
    PSFC = 101000 + np.random.randn(ntime, *lat2d.shape) * 50
    RAINNC = np.cumsum(np.maximum(0, np.random.randn(ntime, *lat2d.shape) * 0.1), axis=0)
    TCDC = np.clip(4 + 2 * np.sin(np.radians(lon2d))[None, :, :], 0, 8)
    if TCDC.shape[0] != ntime:
        TCDC = np.broadcast_to(TCDC, (ntime, *lat2d.shape))

    ds = xr.Dataset(
        data_vars={
            "T2": (["Time", "south_north", "west_east"], T2),
            "U10": (["Time", "south_north", "west_east"], U10),
            "V10": (["Time", "south_north", "west_east"], V10),
            "PSFC": (["Time", "south_north", "west_east"], PSFC),
            "RAINNC": (["Time", "south_north", "west_east"], RAINNC),
            "TCDC": (["Time", "south_north", "west_east"], TCDC),
            "XLAT": (["south_north", "west_east"], lat2d),
            "XLONG": (["south_north", "west_east"], lon2d),
        },
        coords={"Time": times, "south_north": lats, "west_east": lons},
        attrs={"TITLE": "InaNWP sample asim output"},
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(output_path)
    ds.close()
    return output_path


def generate_demo_data() -> dict:
    """Create sample obs + 4 model forecasts for dashboard demo."""
    init_db()
    stations = get_stations()
    init_time = datetime(2026, 7, 1, 12, 0, 0)
    lead_times = list(range(0, 49, 3))
    models = {
        "InaNWP": {"temp_bias": 0.3, "wind_bias": 0.2, "noise": 0.8},
        "InaCAWO": {"temp_bias": 0.1, "wind_bias": 0.1, "noise": 1.0},
        "GFS": {"temp_bias": 0.8, "wind_bias": 0.5, "noise": 1.5},
        "IFS": {"temp_bias": 0.2, "wind_bias": 0.15, "noise": 0.9},
    }

    obs_rows, fcst_rows = [], []
    rng = np.random.default_rng(42)

    for lt in lead_times:
        valid = init_time + timedelta(hours=lt)
        valid_str = valid.strftime("%Y-%m-%dT%H:%M:%S")
        for _, st in stations.iterrows():
            base_temp = 28 - 0.05 * abs(st["lat"]) + rng.normal(0, 0.3)
            base_wind = 4 + rng.normal(0, 0.5)
            base_rh = 75 + rng.normal(0, 5)
            base_mslp = 1010 + rng.normal(0, 1)

            obs_rows.extend([
                {"station_id": st["station_id"], "valid_time": valid_str,
                 "parameter": "temp_drybulb_c_tttttt", "value": base_temp, "qc_flag": "ok"},
                {"station_id": st["station_id"], "valid_time": valid_str,
                 "parameter": "wind_speed_ff", "value": base_wind, "qc_flag": "ok"},
                {"station_id": st["station_id"], "valid_time": valid_str,
                 "parameter": "relative_humidity_pc", "value": base_rh, "qc_flag": "ok"},
                {"station_id": st["station_id"], "valid_time": valid_str,
                 "parameter": "pressure_qff_mb_derived", "value": base_mslp, "qc_flag": "ok"},
                {"station_id": st["station_id"], "valid_time": valid_str,
                 "parameter": "rainfall_6h_rrr", "value": max(0, rng.exponential(2)), "qc_flag": "ok"},
                {"station_id": st["station_id"], "valid_time": valid_str,
                 "parameter": "cloud_cover_oktas_m", "value": rng.integers(0, 9), "qc_flag": "ok"},
            ])

            for model, cfg in models.items():
                fcst_rows.extend([
                    {"model": model, "init_time": init_time.isoformat(), "lead_time": lt,
                     "station_id": st["station_id"], "valid_time": valid_str,
                     "parameter": "temp_drybulb_c_tttttt",
                     "fcst": base_temp + cfg["temp_bias"] + rng.normal(0, cfg["noise"] * 0.3)},
                    {"model": model, "init_time": init_time.isoformat(), "lead_time": lt,
                     "station_id": st["station_id"], "valid_time": valid_str,
                     "parameter": "wind_speed_ff",
                     "fcst": base_wind + cfg["wind_bias"] + rng.normal(0, cfg["noise"] * 0.2)},
                    {"model": model, "init_time": init_time.isoformat(), "lead_time": lt,
                     "station_id": st["station_id"], "valid_time": valid_str,
                     "parameter": "relative_humidity_pc",
                     "fcst": base_rh + rng.normal(0, cfg["noise"] * 2)},
                    {"model": model, "init_time": init_time.isoformat(), "lead_time": lt,
                     "station_id": st["station_id"], "valid_time": valid_str,
                     "parameter": "pressure_qff_mb_derived",
                     "fcst": base_mslp + rng.normal(0, cfg["noise"] * 0.5)},
                    {"model": model, "init_time": init_time.isoformat(), "lead_time": lt,
                     "station_id": st["station_id"], "valid_time": valid_str,
                     "parameter": "rainfall_6h_rrr",
                     "fcst": max(0, rng.exponential(2) + cfg["noise"] * 0.1)},
                    {"model": model, "init_time": init_time.isoformat(), "lead_time": lt,
                     "station_id": st["station_id"], "valid_time": valid_str,
                     "parameter": "cloud_cover_oktas_m",
                     "fcst": float(rng.integers(0, 9))},
                ])

    save_observations(pd.DataFrame(obs_rows))
    save_forecasts(pd.DataFrame(fcst_rows))
    nc_path = generate_sample_nc()

    # Populate precomputed verification scores for demo dashboard
    from backend.config import DB_PATH
    from backend.services.pipeline import init_pipeline_db, save_verification_scores

    init_pipeline_db()
    obs_df = pd.DataFrame(obs_rows)
    fcst_df = pd.DataFrame(fcst_rows)
    from backend.services.verification_cache import (
        compute_run_verification,
        refresh_ranking_cache,
        save_verification_station_scores,
    )
    init_iso = init_time.isoformat()
    scores: list[dict] = []
    station_rows: list[dict] = []
    for model in models:
        s, st = compute_run_verification(model, init_iso, obs_df)
        scores.extend(s)
        station_rows.extend(st)
    save_verification_scores(scores)
    save_verification_station_scores(station_rows)
    refresh_ranking_cache()

    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    for model in models:
        conn.execute(
            "INSERT OR REPLACE INTO model_runs (model, init_time, nc_filename, status, processed_at) VALUES (?,?,?,?,?)",
            (model, init_time.isoformat(), nc_path.name, "done", init_time.isoformat()),
        )
    conn.commit()
    conn.close()

    return {"obs_records": len(obs_rows), "fcst_records": len(fcst_rows), "nc_path": str(nc_path), "scores": len(scores)}
