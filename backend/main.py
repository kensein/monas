"""FastAPI backend — display-only NWP verification dashboard."""
from __future__ import annotations

import sqlite3
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.config import (
    API_PORT,
    BASE_PATH,
    CARTO_API_KEY,
    CORS_ORIGIN,
    LOCAL_NC_PATH,
    MAX_LEAD_TIME_HOURS,
    MODELS,
    SEED_DEMO_DATA,
    VERIFY_PARAMETERS,
)
from backend.services.bmkg_auth import login, token_status
from backend.services.job_manager import create_job, get_job, run_in_background
from backend.services.obs_fetcher import (
    fetch_and_save_sinoptik,
    get_stations,
    init_db,
    load_observations,
)
from backend.services.pipeline import (
    get_available_cycles,
    get_pipeline_status,
    init_pipeline_db,
    load_verification_scores,
    run_full_pipeline,
)
from backend.services.sample_data import generate_demo_data
from backend.services.scheduler import start_scheduler
from backend.services.sftp_client import get_server_model_inventory

app = FastAPI(
    title="NWP Verification API",
    version="2.0.0",
    root_path=BASE_PATH if BASE_PATH else "",
)
_cors_origins = [CORS_ORIGIN, "http://localhost:3013", "http://127.0.0.1:3013"]
if BASE_PATH:
    _cors_origins.append(f"{CORS_ORIGIN}{BASE_PATH}")
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(dict.fromkeys(_cors_origins)),
    allow_methods=["*"],
    allow_headers=["*"],
)


class ObsFetchRequest(BaseModel):
    date_from: str = Field(..., examples=["2025-06-01T00:00:00Z"])
    date_to: str = Field(..., examples=["2025-06-03T23:59:00Z"])
    station_wmo_ids: list[str] | None = None
    parameter_names: list[str] | None = None


@app.on_event("startup")
async def startup() -> None:
    import os
    init_db()
    init_pipeline_db()
    from backend.services.verification_cache import cache_is_stale, init_verification_cache_db, rebuild_dashboard_cache
    init_verification_cache_db()
    if cache_is_stale():
        job = create_job("cache_rebuild")
        run_in_background(job.id, rebuild_dashboard_cache)
    from backend.services.station_catalog import sync_catalog_to_db
    try:
        sync_catalog_to_db()
    except sqlite3.OperationalError:
        pass  # pipeline/scheduler may hold lock briefly at startup
    from backend.services.pipeline import load_verification_scores
    if SEED_DEMO_DATA and load_verification_scores().empty:
        generate_demo_data()
    start_scheduler()
    # Local mode: scan NC lokal sekali (tanpa SFTP) bila path ada
    force = os.getenv("FORCE_PIPELINE", "").lower() == "true"
    local_paths = [
        os.getenv("INANWP_NC_PATH", ""),
        LOCAL_NC_PATH,
    ]
    has_local = any(p and __import__("pathlib").Path(p).exists() for p in local_paths)
    if (force or has_local) and os.getenv("DISABLE_STARTUP_PIPELINE", "false").lower() not in ("1", "true", "yes"):
        job = create_job("pipeline_scan")
        run_in_background(job.id, run_full_pipeline)


@app.post("/api/obs/sync-recent")
async def sync_recent_obs(days: int = Query(10, ge=1, le=30)) -> dict[str, Any]:
    """Fetch recent Sinoptik observations (cron / manual trigger)."""
    from backend.services.obs_fetcher import sync_observations_recent
    return sync_observations_recent(days=days)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "nwp-verification"}


@app.get("/api/pipeline/status")
def pipeline_status() -> dict[str, Any]:
    return get_pipeline_status()


@app.get("/api/pipeline/inventory")
def pipeline_inventory() -> dict[str, Any]:
    return get_server_model_inventory()


@app.post("/api/pipeline/run")
def pipeline_run() -> dict[str, Any]:
    job = create_job("pipeline")
    run_in_background(job.id, run_full_pipeline)
    return {"job_id": job.id, "message": "Pipeline verifikasi HARP dijalankan di background"}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")
    return {
        "id": job.id, "type": job.type, "status": job.status.value,
        "progress": job.progress, "message": job.message, "result": job.result,
    }


@app.get("/api/cycles")
def list_cycles(model: str | None = None) -> list[dict]:
    return get_available_cycles(model)


@app.get("/api/bmkg/token-status")
async def bmkg_token_status() -> dict[str, Any]:
    status = token_status()
    if not status.get("logged_in"):
        try:
            await login()
            status = token_status()
        except Exception as e:
            status["error"] = str(e)
    return status


@app.post("/api/obs/fetch-bmkg")
async def fetch_bmkg_obs(body: ObsFetchRequest) -> dict[str, Any]:
    try:
        return await fetch_and_save_sinoptik(
            body.date_from, body.date_to,
            station_wmo_ids=body.station_wmo_ids,
            parameter_names=body.parameter_names or ["*"],
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/harp/methodology")
def harp_methodology() -> dict[str, Any]:
    from backend.services.harp_methodology import get_methodology
    return get_methodology()


@app.get("/api/models/sources")
def model_sources() -> dict[str, Any]:
    from backend.services.dummy_models import get_model_data_sources
    from backend.config import DUMMY_MODELS, USE_DUMMY_MODELS
    return {
        "sources": get_model_data_sources(),
        "dummy_models": DUMMY_MODELS,
        "use_dummy_models": USE_DUMMY_MODELS,
    }


@app.get("/api/parameters")
def list_parameters() -> dict[str, Any]:
    return {
        "verify_parameters": VERIFY_PARAMETERS,
        "models": MODELS,
        "max_lead_time_hours": MAX_LEAD_TIME_HOURS,
    }


@app.get("/api/config/public")
def public_config() -> dict[str, Any]:
    """Konfigurasi publik untuk frontend (tanpa secret)."""
    return {
        "carto_api_key": CARTO_API_KEY,
        "base_path": BASE_PATH,
    }


@app.get("/api/stations/coverage")
def stations_coverage() -> dict[str, Any]:
    """Cek stasiun observasi yang tidak match katalog WMO."""
    from backend.services.station_catalog import load_station_catalog
    from backend.services.obs_fetcher import get_db

    conn = get_db()
    rows = conn.execute(
        """SELECT o.station_id, COUNT(*) AS n_rows,
                  MIN(o.valid_time) AS t_min, MAX(o.valid_time) AS t_max,
                  s.name
           FROM observations o
           LEFT JOIN stations s ON s.station_id = o.station_id
           GROUP BY o.station_id"""
    ).fetchall()
    conn.close()

    def _is_wmo(sid: str) -> bool:
        return str(sid).isdigit() and len(str(sid)) == 5

    wmo_list, hash_list = [], []
    for r in rows:
        item = {
            "station_id": r["station_id"],
            "name": r["name"],
            "n_rows": r["n_rows"],
            "valid_from": r["t_min"],
            "valid_to": r["t_max"],
        }
        if _is_wmo(r["station_id"]):
            wmo_list.append(item)
        else:
            hash_list.append(item)

    return {
        "catalog_size": len(load_station_catalog()),
        "obs_stations_wmo": len(wmo_list),
        "obs_stations_hash": len(hash_list),
        "hash_stations": sorted(hash_list, key=lambda x: -x["n_rows"])[:50],
        "wmo_sample": wmo_list[:10],
        "hint": "Hash ID = stasiun tidak match katalog saat import; tidak ikut join verifikasi.",
    }


@app.get("/api/stations")
def stations() -> list[dict]:
    return get_stations().to_dict(orient="records")


def _scores_to_list(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    return df.round(4).to_dict(orient="records")


@app.get("/api/verification/scores")
def verification_scores(
    models: str = Query("InaNWP,InaCAWO,GFS,IFS"),
    parameter: str = Query("temp_drybulb_c_tttttt"),
    init_time: str | None = None,
    lead_time: int | None = None,
) -> dict[str, Any]:
    model_list = [m.strip() for m in models.split(",") if m.strip()]
    df = load_verification_scores(models=model_list, parameter=parameter, init_time=init_time, lead_time=lead_time)

    if df.empty:
        raise HTTPException(status_code=404, detail="Belum ada skor verifikasi — tunggu pipeline auto-sync")

    meta = VERIFY_PARAMETERS.get(parameter, {"label": parameter, "unit": "", "category": "continuous"})
    return {"parameter": parameter, "meta": meta, "scores": _scores_to_list(df)}


@app.get("/api/verification/ranking")
def verification_ranking(
    models: str = Query("InaNWP,InaCAWO,GFS,IFS"),
    init_time: str | None = None,
    score: str = Query("rmse"),
) -> dict[str, Any]:
    from backend.services.verification_cache import get_or_build_ranking

    model_list = [m.strip() for m in models.split(",") if m.strip()]
    payload = get_or_build_ranking(model_list, init_time=init_time, score=score)
    if not payload:
        raise HTTPException(status_code=404, detail="Belum ada data ranking")
    return payload


@app.get("/api/verification/map")
def verification_map(
    model: str = Query("InaNWP"),
    parameter: str = Query("temp_drybulb_c_tttttt"),
    lead_time: int = Query(12),
    init_time: str | None = None,
) -> list[dict]:
    from backend.services.verification_cache import load_verification_station_scores

    stats = load_verification_station_scores(model, parameter, lead_time, init_time=init_time)
    if stats.empty:
        return []

    stations_df = get_stations()
    return stats.merge(stations_df, on="station_id", how="left").round(4).to_dict(orient="records")


@app.get("/api/station/{station_id}/detail")
def station_detail(
    station_id: str,
    parameter: str = Query("temp_drybulb_c_tttttt"),
    models: str = Query("InaNWP,InaCAWO,GFS,IFS"),
    init_time: str | None = None,
) -> dict[str, Any]:
    from backend.services.obs_fetcher import load_forecasts
    from backend.services.time_utils import normalize_valid_time

    model_list = [m.strip() for m in models.split(",") if m.strip()]
    obs_df = load_observations(parameters=[parameter], station_id=station_id)
    obs_df["valid_time"] = obs_df["valid_time"].map(normalize_valid_time)
    fcst_df = load_forecasts(
        models=model_list, parameters=[parameter], init_time=init_time, station_id=station_id,
    )
    fcst_df["valid_time"] = fcst_df["valid_time"].map(normalize_valid_time)

    times = sorted(set(obs_df["valid_time"].dropna()) | set(fcst_df["valid_time"].dropna()))
    series = []
    for vt in times:
        entry: dict[str, Any] = {"valid_time": vt}
        obs_row = obs_df[obs_df["valid_time"] == vt]
        if not obs_row.empty:
            entry["obs"] = float(obs_row.iloc[0]["value"])
        for model in model_list:
            match = fcst_df[(fcst_df["model"] == model) & (fcst_df["valid_time"] == vt)]
            if not match.empty:
                entry[model] = float(match.iloc[0]["fcst"])
                entry[f"{model}_lead"] = int(match.iloc[0]["lead_time"])
        if "obs" in entry or any(m in entry for m in model_list):
            series.append(entry)

    st = get_stations()
    info = st[st["station_id"] == station_id]
    return {
        "station": info.to_dict(orient="records")[0] if not info.empty else {"station_id": station_id},
        "parameter": parameter,
        "init_time": init_time,
        "series": series,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=API_PORT, reload=False)
