"""FastAPI backend — display-only NWP verification dashboard."""
from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.config import (
    API_PORT,
    MAX_LEAD_TIME_HOURS,
    MODELS,
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
from backend.services.verification import compute_ranking

app = FastAPI(title="NWP Verification API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    init_db()
    init_pipeline_db()
    from backend.services.pipeline import load_verification_scores
    if load_verification_scores().empty:
        generate_demo_data()
    start_scheduler()
    # Initial pipeline scan (background) — skip 12GB download from cloud; runs fully on litbangweb
    import os
    if os.path.exists("/opt/lampp/htdocs/wrf/wrfout") or os.getenv("FORCE_PIPELINE", "").lower() == "true":
        job = create_job("pipeline_scan")
        run_in_background(job.id, run_full_pipeline)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "auto-sync-litbangweb"}


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


@app.get("/api/parameters")
def list_parameters() -> dict[str, Any]:
    return {
        "verify_parameters": VERIFY_PARAMETERS,
        "models": MODELS,
        "max_lead_time_hours": MAX_LEAD_TIME_HOURS,
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
    model_list = [m.strip() for m in models.split(",") if m.strip()]
    df = load_verification_scores(models=model_list, init_time=init_time)

    if df.empty:
        raise HTTPException(status_code=404, detail="Belum ada data ranking")

    from backend.services.verification import VerificationResult
    results = [
        VerificationResult(
            parameter=row["parameter"], model=row["model"], lead_time=int(row["lead_time"]),
            n_cases=int(row["n_cases"]), n_stations=int(row["n_stations"]),
            bias=row["bias"], rmse=row["rmse"], mae=row["mae"],
            stde=row["stde"], correlation=row["correlation"],
        )
        for _, row in df.iterrows()
    ]
    ranking = compute_ranking(results, score=score)
    return {"score_metric": score, "init_time": init_time, "ranking": ranking}


@app.get("/api/verification/map")
def verification_map(
    model: str = Query("InaNWP"),
    parameter: str = Query("temp_drybulb_c_tttttt"),
    lead_time: int = Query(12),
    init_time: str | None = None,
) -> list[dict]:
    from backend.services.obs_fetcher import load_forecasts
    from backend.services.verification import build_verification_pairs

    obs_df = load_observations(parameters=[parameter])
    fcst_df = load_forecasts(models=[model], parameters=[parameter])
    if init_time:
        fcst_df = fcst_df[fcst_df["init_time"] == init_time]
    fcst_df = fcst_df[fcst_df["lead_time"] == lead_time]
    stations_df = get_stations()

    circular = VERIFY_PARAMETERS.get(parameter, {}).get("category") == "circular"
    pairs = build_verification_pairs(obs_df, fcst_df, parameter, circular=circular)
    if pairs.empty:
        return []

    pairs["error"] = pairs["fcst"] - pairs["obs"]
    stats = pairs.groupby("station_id").agg(
        rmse=("error", lambda e: float((e ** 2).mean()) ** 0.5),
        bias=("error", "mean"),
        n_cases=("fcst", "count"),
        obs_mean=("obs", "mean"),
        fcst_mean=("fcst", "mean"),
    ).reset_index()
    return stats.merge(stations_df, on="station_id", how="left").round(4).to_dict(orient="records")


@app.get("/api/station/{station_id}/detail")
def station_detail(
    station_id: str,
    parameter: str = Query("temp_drybulb_c_tttttt"),
    models: str = Query("InaNWP,InaCAWO,GFS,IFS"),
    init_time: str | None = None,
) -> dict[str, Any]:
    from backend.services.obs_fetcher import load_forecasts

    model_list = [m.strip() for m in models.split(",") if m.strip()]
    obs_df = load_observations(parameters=[parameter])
    obs_df = obs_df[obs_df["station_id"] == station_id].sort_values("valid_time")
    fcst_df = load_forecasts(models=model_list, parameters=[parameter])
    if init_time:
        fcst_df = fcst_df[fcst_df["init_time"] == init_time]
    fcst_df = fcst_df[fcst_df["station_id"] == station_id].sort_values("valid_time")

    series = []
    for _, row in obs_df.iterrows():
        entry = {"valid_time": row["valid_time"], "obs": row["value"]}
        for model in model_list:
            match = fcst_df[(fcst_df["model"] == model) & (fcst_df["valid_time"] == row["valid_time"])]
            if not match.empty:
                entry[model] = float(match.iloc[0]["fcst"])
                entry[f"{model}_lead"] = int(match.iloc[0]["lead_time"])
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
