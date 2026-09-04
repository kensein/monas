"""HARP compute v2 — NC (crop) → interpolasi titik → join obs → det_verify → f32 store.

Alur (metode HARP tidak berubah, hanya I/O):
  1. discover NC per model (folder crop, mis. /data/nc)
  2. obs: sinoptik JSON → cube bulanan f32 (hanya VERIFY_PARAMETERS), cache per bulan
  3. per run: read_point_forecast (interpolasi grid→stasiun) → array [param, lead, station]
  4. obs paired di valid_time = init + lead
  5. det_verify per (param, lead) → scores [param, lead, metric]
  6. tulis runs/<model>/<init>/{fcst,obs,scores}.f32 + meta.json, rebuild manifest
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from backend.config import MAX_LEAD_TIME_HOURS, MODEL_LOCAL_PATHS, VERIFY_PARAMETERS
from backend.services import harp_store as hs
from backend.services.nc_reader import parse_init_time, read_point_forecast
from backend.services.verification import build_verification_pairs, det_verify

Log = Callable[[str], None]


def _log_default(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _infer_model(filename: str) -> str:
    n = filename.lower()
    if "-cawo" in n:
        return "InaCAWO"
    if "-gfs" in n:
        return "GFS"
    if "-ifs" in n:
        return "IFS"
    return "InaNWP"


def discover_nc(extra_dirs: list[Path] | None = None) -> list[dict[str, Any]]:
    """Scan folder NC (crop) per model → daftar {model, init_time, path, size}."""
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    dirs: list[tuple[str | None, Path, str]] = []
    for model, cfg in MODEL_LOCAL_PATHS.items():
        dirs.append((model, Path(cfg["path"]), cfg.get("pattern", "*.nc")))
    for d in extra_dirs or []:
        dirs.append((None, Path(d), "*.nc"))
    for model, d, pattern in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob(pattern)):
            if not f.is_file() or f.name.endswith(".tmp") or ".tmp." in f.name:
                continue
            init = parse_init_time(f.name)
            if not init:
                continue
            m = model or _infer_model(f.name)
            key = (m, init.isoformat())
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "model": m,
                "init_time": init.isoformat(),
                "path": f,
                "size": f.stat().st_size,
                "filename": f.name,
            })
    out.sort(key=lambda r: r["init_time"], reverse=True)
    return out


def months_for_runs(runs: list[dict[str, Any]]) -> set[str]:
    months: set[str] = set()
    for r in runs:
        init = hs._parse_dt(r["init_time"])
        end = init + timedelta(hours=MAX_LEAD_TIME_HOURS)
        cur = init.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        while cur <= end:
            months.add(cur.strftime("%Y%m"))
            cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


def _fcst_records_to_cube(
    fcst_df: pd.DataFrame,
    params: list[str],
    sids: list[str],
) -> tuple[np.ndarray, list[int]]:
    """DataFrame (station_id, lead_time, parameter, fcst) → [n_param, n_lead, n_station]."""
    if fcst_df.empty:
        return np.zeros((len(params), 0, len(sids)), dtype=hs.F32), []
    leads = sorted(int(x) for x in fcst_df["lead_time"].unique() if 0 <= int(x) <= MAX_LEAD_TIME_HOURS)
    lidx = {lt: i for i, lt in enumerate(leads)}
    pidx = {p: i for i, p in enumerate(params)}
    sidx = {s: i for i, s in enumerate(sids)}
    cube = np.full((len(params), len(leads), len(sids)), np.nan, dtype=hs.F32)
    df = fcst_df[fcst_df["parameter"].isin(params)]
    p_i = df["parameter"].map(pidx)
    l_i = df["lead_time"].astype(int).map(lidx)
    s_i = df["station_id"].astype(str).map(sidx)
    ok = p_i.notna() & l_i.notna() & s_i.notna()
    cube[p_i[ok].astype(int).values, l_i[ok].astype(int).values, s_i[ok].astype(int).values] = (
        df.loc[ok, "fcst"].astype(float).values
    )
    return cube, leads


def compute_run(
    model: str,
    init_time: str,
    nc_path: Path,
    obs: hs.ObsCube,
    log: Log = _log_default,
) -> dict[str, Any]:
    t0 = time.time()
    params = list(VERIFY_PARAMETERS.keys())
    sids = obs.station_ids
    stations = hs.station_frame()
    init_dt = hs._parse_dt(init_time)

    def prog(p: float, msg: str) -> None:
        log(f"  [{p:5.1f}%] {msg}")

    log(f"── {model} init {init_time} · {nc_path.name} ({nc_path.stat().st_size / 1e6:.0f} MB)")
    prog(5, "Interpolasi grid → titik stasiun (semua param InaNWP)...")
    fcst_df = read_point_forecast(nc_path, model, stations, params, init_time=init_dt, progress_cb=prog)
    fcst, leads = _fcst_records_to_cube(fcst_df, params, sids)
    prog(45, f"Forecast cube: {fcst.shape} ({int(np.sum(~np.isnan(fcst)))} nilai), {len(leads)} lead")

    obs_cube = np.full_like(fcst, np.nan)
    from backend.services.obs_qc import mask_obs_sentinels

    for li, lt in enumerate(leads):
        vt = init_dt + timedelta(hours=int(lt))
        for pi, p in enumerate(params):
            obs_cube[pi, li] = mask_obs_sentinels(obs.slice_at(vt, p)).astype(hs.F32)
    n_pairs = int(np.sum(~np.isnan(fcst) & ~np.isnan(obs_cube)))
    prog(55, f"Pasangan fcst↔obs: {n_pairs}")

    scores = np.full((len(params), len(leads), len(hs.SCORE_METRICS)), np.nan, dtype=hs.F32)
    for pi, p in enumerate(params):
        circular = VERIFY_PARAMETERS[p].get("category") == "circular"
        for li, lt in enumerate(leads):
            f = fcst[pi, li]
            o = obs_cube[pi, li]
            ok = ~np.isnan(f) & ~np.isnan(o)
            if ok.sum() < 2:
                continue
            vt = hs.iso_z(init_dt + timedelta(hours=int(lt)))
            pairs_obs = pd.DataFrame({
                "station_id": np.asarray(sids)[ok], "valid_time": vt,
                "parameter": p, "value": o[ok].astype(float),
            })
            pairs_fcst = pd.DataFrame({
                "station_id": np.asarray(sids)[ok], "valid_time": vt, "fcst": f[ok].astype(float),
            })
            pairs = build_verification_pairs(pairs_obs, pairs_fcst, p, circular=circular)
            vr = det_verify(pairs, p, model, int(lt), circular=circular)
            if vr is None:
                continue
            scores[pi, li] = [
                vr.bias, vr.rmse, vr.mae, vr.stde, vr.correlation, vr.n_cases, vr.n_stations,
            ]
        prog(55 + (pi + 1) / len(params) * 35, f"det_verify {p} selesai")

    d = hs.write_run(
        model, init_time, params, leads, sids, fcst, obs_cube, scores,
        nc_info={"filename": nc_path.name, "size": nc_path.stat().st_size, "path": str(nc_path)},
        extra={"n_pairs": n_pairs, "elapsed_sec": round(time.time() - t0, 1)},
    )
    prog(100, f"Tersimpan → {d} ({time.time() - t0:.0f}s)")
    return {"model": model, "init_time": init_time, "n_pairs": n_pairs, "leads": len(leads), "dir": str(d)}


def run_pipeline(
    obs_json_dir: Path,
    max_runs: int = 1,
    force: bool = False,
    force_obs: bool = False,
    keep_runs_per_model: int = 0,
    models: list[str] | None = None,
    log: Log = _log_default,
) -> dict[str, Any]:
    t0 = time.time()
    log("1/4 Scan NC crop...")
    discovered = discover_nc()
    if models:
        discovered = [r for r in discovered if r["model"] in models]
    log(f"   {len(discovered)} NC ditemukan: " + ", ".join(f"{r['model']}@{r['init_time'][:13]}" for r in discovered[:8]))

    pending = [r for r in discovered if force or not hs.run_exists_for_nc(r["model"], r["init_time"], r["path"])]
    log(f"   pending={len(pending)} (sudah ada di store: {len(discovered) - len(pending)})")
    if max_runs > 0:
        pending = pending[:max_runs]
        log(f"   proses {len(pending)} run terbaru (max_runs={max_runs})")

    result: dict[str, Any] = {"discovered": len(discovered), "processed": [], "errors": []}
    if not pending:
        hs.rebuild_manifest()
        log("   Tidak ada run baru — manifest diperbarui")
        result["elapsed_sec"] = round(time.time() - t0, 1)
        return result

    log("2/4 Observasi → cube f32 per bulan (hanya param HARP)...")
    months = months_for_runs(pending)
    obs_status = hs.ensure_obs_months(obs_json_dir, months, force=force_obs, log=log)
    result["obs_months"] = obs_status
    obs = hs.ObsCube()

    log("3/4 Verifikasi HARP per run (serial, live log)...")
    for i, r in enumerate(pending, 1):
        log(f"[{i}/{len(pending)}]")
        try:
            result["processed"].append(compute_run(r["model"], r["init_time"], r["path"], obs, log=log))
        except Exception as e:  # noqa: BLE001
            log(f"  ERROR {r['model']} {r['init_time']}: {e}")
            result["errors"].append({"model": r["model"], "init_time": r["init_time"], "error": str(e)})

    log("4/4 Manifest + prune...")
    if keep_runs_per_model > 0:
        removed = hs.prune_runs(keep_runs_per_model)
        log(f"   prune: {removed} run lama dihapus (keep {keep_runs_per_model}/model)")
    m = hs.rebuild_manifest()
    size_mb = hs.store_size_bytes() / 1e6
    log(f"   manifest: {len(m['runs'])} run · store {size_mb:.0f} MB → {hs.store_root()}")
    result["manifest_runs"] = len(m["runs"])
    result["store_mb"] = round(size_mb, 1)
    result["elapsed_sec"] = round(time.time() - t0, 1)
    log(f"Selesai dalam {result['elapsed_sec']/60:.1f} menit")
    return result
