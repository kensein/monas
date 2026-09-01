"""FastAPI backend for NWP verification dashboard."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.config import (
    API_PORT,
    MODELS,
    NC_DIR,
    VERIFY_PARAMETERS,
)
from backend.services.bmkg_auth import login, token_status
from backend.services.job_manager import JobStatus, create_job, get_job, run_in_background
from backend.services.nc_ingest import ingest_nc_from_path, resolve_local_path
from backend.services.nc_reader import inspect_nc, read_point_forecast
from backend.services.obs_fetcher import (
    fetch_and_save_sinoptik,
    get_stations,
    init_db,
    load_forecasts,
    load_observations,
    save_forecasts,
    sync_obs_from_sftp,
)
from backend.services.sample_data import generate_demo_data
from backend.services.verification import (
    build_verification_pairs,
    compute_ranking,
    det_verify,
)

app = FastAPI(title="NWP Verification API", version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class LocalPathRequest(BaseModel):
    path: str = Field(..., description="Absolute path ke file .nc di komputer lokal")
    model: str = "InaNWP"
    copy_to_data_dir: bool = False


class ObsFetchRequest(BaseModel):
    date_from: str = Field(..., examples=["2025-06-01T00:00:00Z"])
    date_to: str = Field(..., examples=["2025-06-03T23:59:00Z"])
    station_wmo_ids: list[str] | None = None
    parameter_names: list[str] | None = None


@app.on_event("startup")
async def startup() -> None:
    init_db()
    if load_observations().empty:
        generate_demo_data()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/bmkg/token-status")
async def bmkg_token_status() -> dict[str, Any]:
    status = token_status()
    if not status.get("logged_in"):
        try:
            await login()
            status = token_status()
            status["just_logged_in"] = True
        except Exception as e:
            status["error"] = str(e)
    return status


@app.post("/api/bmkg/refresh-token")
async def bmkg_refresh_token() -> dict[str, Any]:
    try:
        await login(force=True)
        return {"ok": True, **token_status()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/parameters")
def list_parameters() -> dict[str, Any]:
    return {"verify_parameters": VERIFY_PARAMETERS, "models": MODELS}


@app.get("/api/stations")
def stations() -> list[dict]:
    return get_stations().to_dict(orient="records")


@app.post("/api/obs/fetch-bmkg")
async def fetch_bmkg_obs(body: ObsFetchRequest) -> dict[str, Any]:
    """Fetch observasi Sinoptik real via POST export API (semua parameter)."""
    try:
        return await fetch_and_save_sinoptik(
            body.date_from, body.date_to,
            station_wmo_ids=body.station_wmo_ids,
            parameter_names=body.parameter_names or ["*"],
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/obs/sync-sftp")
def sync_sftp() -> dict[str, Any]:
    try:
        return sync_obs_from_sftp()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/models/load-local-path")
async def load_local_nc(body: LocalPathRequest) -> dict[str, Any]:
    """
    Baca file NC langsung dari path lokal (untuk file besar 11GB+).
    Jalankan backend di komputer yang punya file tersebut.
    """
    if body.model not in MODELS:
        raise HTTPException(status_code=400, detail=f"Model harus salah satu dari {MODELS}")

    try:
        path = resolve_local_path(body.path)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=404, detail=str(e))

    job = create_job("nc_ingest")
    run_in_background(
        job.id, ingest_nc_from_path, path, body.model, body.copy_to_data_dir,
    )
    return {
        "job_id": job.id,
        "message": f"Memproses {path.name} ({path.stat().st_size / 1e9:.2f} GB) di background",
        "path": str(path),
    }


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")
    return {
        "id": job.id,
        "type": job.type,
        "status": job.status.value,
        "progress": job.progress,
        "message": job.message,
        "result": job.result,
    }


@app.post("/api/models/upload-nc")
async def upload_nc(
    file: UploadFile = File(...),
    model: str = Query("InaNWP"),
) -> dict[str, Any]:
    if model not in MODELS:
        raise HTTPException(status_code=400, detail=f"Model harus salah satu dari {MODELS}")

    dest = NC_DIR / (file.filename or "upload.nc")
    job = create_job("nc_upload")

    # Simpan file dulu (sync) agar stream tidak hilang saat background thread
    with open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)

    run_in_background(
        job.id, ingest_nc_from_path, dest, model, False,
    )
    return {
        "job_id": job.id,
        "filename": dest.name,
        "size_gb": round(dest.stat().st_size / 1e9, 2),
        "message": "Upload selesai, proses interpolasi di background",
    }


@app.post("/api/models/load-default-local")
async def load_default_local(model: str = Query("InaNWP")) -> dict[str, Any]:
    """Load NC from LOCAL_NC_PATH env var."""
    path_str = os.getenv("LOCAL_NC_PATH", "")
    if not path_str:
        raise HTTPException(status_code=400, detail="LOCAL_NC_PATH belum diset di .env")
    return await load_local_nc(LocalPathRequest(path=path_str, model=model))


@app.post("/api/demo/seed")
def seed_demo() -> dict[str, Any]:
    return generate_demo_data()


@app.get("/api/verification/scores")
def verification_scores(
    models: str = Query("InaNWP,InaCAWO,GFS,IFS"),
    parameter: str = Query("temp_drybulb_c_tttttt"),
    lead_time: int | None = None,
) -> dict[str, Any]:
    model_list = [m.strip() for m in models.split(",") if m.strip()]
    obs_df = load_observations(parameters=[parameter])
    fcst_df = load_forecasts(models=model_list, parameters=[parameter])

    if obs_df.empty or fcst_df.empty:
        raise HTTPException(status_code=404, detail="Data observasi/prakiraan tidak ditemukan")

    meta = VERIFY_PARAMETERS.get(parameter, {"label": parameter, "unit": "", "category": "continuous"})
    circular = meta.get("category") == "circular"
    results = []

    for model in model_list:
        mfcst = fcst_df[fcst_df["model"] == model]
        if lead_time is not None:
            mfcst = mfcst[mfcst["lead_time"] == lead_time]

        for lt in sorted(mfcst["lead_time"].unique()):
            lt_fcst = mfcst[mfcst["lead_time"] == lt]
            pairs = build_verification_pairs(obs_df, lt_fcst, parameter, circular=circular)
            vr = det_verify(pairs, parameter, model, int(lt), circular=circular)
            if vr:
                results.append(vr.to_dict())

    return {"parameter": parameter, "meta": meta, "scores": results}


@app.get("/api/verification/ranking")
def verification_ranking(
    models: str = Query("InaNWP,InaCAWO,GFS,IFS"),
    score: str = Query("rmse"),
) -> dict[str, Any]:
    model_list = [m.strip() for m in models.split(",") if m.strip()]
    all_results = []

    for param in VERIFY_PARAMETERS:
        obs_df = load_observations(parameters=[param])
        fcst_df = load_forecasts(models=model_list, parameters=[param])
        if obs_df.empty or fcst_df.empty:
            continue
        circular = VERIFY_PARAMETERS[param].get("category") == "circular"
        for model in model_list:
            mfcst = fcst_df[fcst_df["model"] == model]
            for lt in mfcst["lead_time"].unique():
                lt_fcst = mfcst[mfcst["lead_time"] == lt]
                pairs = build_verification_pairs(obs_df, lt_fcst, param, circular=circular)
                vr = det_verify(pairs, param, model, int(lt), circular=circular)
                if vr:
                    all_results.append(vr)

    ranking = compute_ranking(all_results, score=score)
    return {"score_metric": score, "ranking": ranking}


@app.get("/api/verification/map")
def verification_map(
    model: str = Query("InaNWP"),
    parameter: str = Query("temp_drybulb_c_tttttt"),
    lead_time: int = Query(12),
) -> list[dict]:
    obs_df = load_observations(parameters=[parameter])
    fcst_df = load_forecasts(models=[model], parameters=[parameter])
    fcst_df = fcst_df[fcst_df["lead_time"] == lead_time]
    stations_df = get_stations()

    circular = VERIFY_PARAMETERS.get(parameter, {}).get("category") == "circular"
    pairs = build_verification_pairs(obs_df, fcst_df, parameter, circular=circular)

    if pairs.empty:
        return []

    pairs = pairs.copy()
    pairs["error"] = pairs["fcst"] - pairs["obs"]
    station_stats = pairs.groupby("station_id").agg(
        rmse=("error", lambda e: float((e ** 2).mean()) ** 0.5),
        bias=("error", "mean"),
        n_cases=("fcst", "count"),
        obs_mean=("obs", "mean"),
        fcst_mean=("fcst", "mean"),
    ).reset_index()

    merged = station_stats.merge(stations_df, on="station_id", how="left")
    return merged.round(4).to_dict(orient="records")


@app.get("/api/station/{station_id}/detail")
def station_detail(
    station_id: str,
    parameter: str = Query("temp_drybulb_c_tttttt"),
    models: str = Query("InaNWP,InaCAWO,GFS,IFS"),
) -> dict[str, Any]:
    model_list = [m.strip() for m in models.split(",") if m.strip()]
    obs_df = load_observations(parameters=[parameter])
    obs_df = obs_df[obs_df["station_id"] == station_id].sort_values("valid_time")

    fcst_df = load_forecasts(models=model_list, parameters=[parameter])
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

    stations_df = get_stations()
    st_info = stations_df[stations_df["station_id"] == station_id]
    return {
        "station": st_info.to_dict(orient="records")[0] if not st_info.empty else {"station_id": station_id},
        "parameter": parameter,
        "series": series,
    }


@app.get("/api/nc/list")
def list_nc() -> list[dict]:
    files = []
    for p in NC_DIR.glob("*.nc"):
        files.append({"filename": p.name, "size": p.stat().st_size, "info": inspect_nc(p)})
    return files


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=API_PORT, reload=False)
