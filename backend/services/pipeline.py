"""Auto-sync model NC from litbangweb server and run HARP verification pipeline."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from backend.config import (
    DB_PATH,
    LOCAL_NC_PATH,
    MAX_LEAD_TIME_HOURS,
    MODEL_LOCAL_PATHS,
    VERIFY_PARAMETERS,
)
from backend.services.nc_ingest import ingest_nc_from_path
from backend.services.nc_reader import parse_init_time
from backend.services.obs_fetcher import get_stations, load_observations, sync_observations_for_init
from backend.services.sftp_client import list_remote_nc_files, resolve_model_nc_path
from backend.services.verification import build_verification_pairs, det_verify


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_pipeline_db() -> None:
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS model_runs (
            model TEXT NOT NULL,
            init_time TEXT NOT NULL,
            nc_filename TEXT,
            nc_path TEXT,
            file_size INTEGER,
            status TEXT DEFAULT 'pending',
            processed_at TEXT,
            error_message TEXT,
            PRIMARY KEY (model, init_time)
        );
        CREATE TABLE IF NOT EXISTS verification_scores (
            model TEXT NOT NULL,
            parameter TEXT NOT NULL,
            init_time TEXT NOT NULL,
            lead_time INTEGER NOT NULL,
            bias REAL, rmse REAL, mae REAL, stde REAL, correlation REAL,
            n_cases INTEGER, n_stations INTEGER,
            computed_at TEXT,
            PRIMARY KEY (model, parameter, init_time, lead_time)
        );
        CREATE TABLE IF NOT EXISTS pipeline_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT, finished_at TEXT,
            status TEXT, message TEXT, details TEXT
        );
    """)
    conn.commit()
    conn.close()


def _infer_model_from_filename(filename: str) -> str:
    name = filename.lower()
    if "-cawo" in name:
        return "InaCAWO"
    if "-gfs" in name:
        return "GFS"
    if "-ifs" in name:
        return "IFS"
    return "InaNWP"


def _discover_local_nc_file(path_str: str) -> list[dict[str, Any]]:
    """Register a single LOCAL_NC_PATH file (Opsi B dev)."""
    p = Path(path_str.strip().strip('"').strip("'"))
    if not p.is_file() or p.suffix.lower() != ".nc":
        return []
    init = parse_init_time(p.name)
    if not init:
        return []
    model = _infer_model_from_filename(p.name)
    return [{
        "model": model,
        "init_time": init.isoformat(),
        "nc_filename": p.name,
        "nc_path": str(p.resolve()),
        "file_size": p.stat().st_size,
        "accessible": True,
        "source": "local_nc_path",
    }]


def discover_model_runs() -> list[dict[str, Any]]:
    """Scan configured paths (local server, LOCAL_NC_PATH, or SFTP) for NC files."""
    runs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    if LOCAL_NC_PATH:
        for r in _discover_local_nc_file(LOCAL_NC_PATH):
            key = (r["model"], r["init_time"])
            if key not in seen:
                runs.append(r)
                seen.add(key)

    for model, cfg in MODEL_LOCAL_PATHS.items():
        files = list_remote_nc_files(cfg["path"], pattern=cfg.get("pattern", "*.nc"))
        for f in files:
            init = parse_init_time(f["filename"])
            if not init:
                continue
            key = (model, init.isoformat())
            if key in seen:
                continue
            runs.append({
                "model": model,
                "init_time": init.isoformat(),
                "nc_filename": f["filename"],
                "nc_path": f["path"],
                "file_size": f.get("size", 0),
                "accessible": f.get("accessible", False),
                "source": f.get("source", "scan"),
            })
            seen.add(key)

    return sorted(runs, key=lambda x: x["init_time"], reverse=True)


def register_runs(runs: list[dict]) -> int:
    conn = get_db()
    n = 0
    for r in runs:
        conn.execute(
            """INSERT OR IGNORE INTO model_runs
               (model, init_time, nc_filename, nc_path, file_size, status)
               VALUES (?, ?, ?, ?, ?, 'pending')""",
            (r["model"], r["init_time"], r["nc_filename"], r["nc_path"], r.get("file_size", 0)),
        )
        conn.execute(
            """UPDATE model_runs SET nc_filename=?, nc_path=?, file_size=?
               WHERE model=? AND init_time=?""",
            (r["nc_filename"], r["nc_path"], r.get("file_size", 0), r["model"], r["init_time"]),
        )
        n += 1
    conn.commit()
    conn.close()
    return n


def get_pending_runs() -> pd.DataFrame:
    conn = get_db()
    df = pd.read_sql_query(
        "SELECT * FROM model_runs WHERE status IN ('pending','error') ORDER BY init_time DESC",
        conn,
    )
    conn.close()
    return df


def get_available_cycles(model: str | None = None) -> list[dict]:
    init_pipeline_db()
    conn = get_db()
    q = "SELECT model, init_time, nc_filename, status, processed_at FROM model_runs"
    params: list[Any] = []
    if model:
        q += " WHERE model=?"
        params.append(model)
    q += " ORDER BY init_time DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_verification_scores(scores: list[dict]) -> int:
    conn = get_db()
    ts = datetime.utcnow().isoformat()
    for s in scores:
        if s.get("lead_time", 0) > MAX_LEAD_TIME_HOURS:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO verification_scores
               (model, parameter, init_time, lead_time, bias, rmse, mae, stde,
                correlation, n_cases, n_stations, computed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (s["model"], s["parameter"], s["init_time"], s["lead_time"],
             s.get("bias"), s.get("rmse"), s.get("mae"), s.get("stde"),
             s.get("correlation"), s.get("n_cases"), s.get("n_stations"), ts),
        )
    conn.commit()
    conn.close()
    return len(scores)


def load_verification_scores(
    models: list[str] | None = None,
    parameter: str | None = None,
    init_time: str | None = None,
    lead_time: int | None = None,
) -> pd.DataFrame:
    init_pipeline_db()
    conn = get_db()
    q = "SELECT * FROM verification_scores WHERE lead_time <= ?"
    params: list[Any] = [MAX_LEAD_TIME_HOURS]
    if models:
        q += f" AND model IN ({','.join('?' * len(models))})"
        params.extend(models)
    if parameter:
        q += " AND parameter=?"
        params.append(parameter)
    if init_time:
        q += " AND init_time=?"
        params.append(init_time)
    if lead_time is not None:
        q += " AND lead_time=?"
        params.append(lead_time)
    q += " ORDER BY lead_time"
    df = pd.read_sql_query(q, conn, params=params)
    conn.close()
    return df


def _ensure_observations(init_time: str, progress_cb: Callable[[float, str], None] | None) -> dict[str, Any]:
    """Fetch BMKG Sinoptik obs for init cycle if cache is empty or stale."""
    obs_df = load_observations()
    init_dt = datetime.fromisoformat(init_time.replace("Z", ""))
    window_start = (init_dt - timedelta(hours=6)).isoformat()
    window_end = (init_dt + timedelta(hours=MAX_LEAD_TIME_HOURS + 6)).isoformat()

    has_window = False
    if not obs_df.empty:
        in_window = obs_df[
            (obs_df["valid_time"] >= window_start) & (obs_df["valid_time"] <= window_end)
        ]
        has_window = len(in_window) > 100

    if has_window:
        return {"skipped": True, "records_saved": len(obs_df)}

    if progress_cb:
        progress_cb(55, f"Fetch observasi BMKG D+0–D+7 untuk init {init_time}...")
    result = sync_observations_for_init(init_time)
    if result.get("error"):
        if not obs_df.empty:
            return {"skipped": False, "warning": result["error"], "records_saved": len(obs_df)}
        raise ValueError(f"Gagal fetch observasi BMKG: {result['error']}")
    return result


def process_model_run(
    model: str,
    init_time: str,
    nc_path: str,
    progress_cb: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Full HARP pipeline for one model run: read NC → join obs → verify all lead times."""
    def report(p: float, msg: str) -> None:
        if progress_cb:
            progress_cb(p, msg)

    conn = get_db()
    conn.execute(
        "UPDATE model_runs SET status='processing' WHERE model=? AND init_time=?",
        (model, init_time),
    )
    conn.commit()
    conn.close()

    try:
        local_path = resolve_model_nc_path(nc_path)
        report(10, f"Membaca NC: {local_path.name} ({local_path.stat().st_size / 1e9:.2f} GB)")

        ingest_result = ingest_nc_from_path(local_path, model, copy_to_data_dir=False, progress_cb=report)

        obs_sync = _ensure_observations(init_time, progress_cb)
        report(70, "Menghitung skor verifikasi HARP D+0–D+7...")
        obs_df = load_observations()
        if obs_df.empty:
            raise ValueError("Data observasi kosong — jalankan sync observasi terlebih dahulu")

        all_scores = []
        for param, meta in VERIFY_PARAMETERS.items():
            circular = meta.get("category") == "circular"
            param_obs = obs_df[obs_df["parameter"] == param]
            if param_obs.empty:
                continue

            from backend.services.obs_fetcher import load_forecasts
            fcst_df = load_forecasts(models=[model], parameters=[param])
            fcst_df = fcst_df[fcst_df["init_time"] == init_time]
            fcst_df = fcst_df[fcst_df["lead_time"] <= MAX_LEAD_TIME_HOURS]

            for lt in sorted(fcst_df["lead_time"].unique()):
                lt_fcst = fcst_df[fcst_df["lead_time"] == lt]
                pairs = build_verification_pairs(param_obs, lt_fcst, param, circular=circular)
                vr = det_verify(pairs, param, model, int(lt), circular=circular)
                if vr:
                    d = vr.to_dict()
                    d["init_time"] = init_time
                    all_scores.append(d)

        saved = save_verification_scores(all_scores)

        conn = get_db()
        conn.execute(
            """UPDATE model_runs SET status='done', processed_at=?, error_message=NULL
               WHERE model=? AND init_time=?""",
            (datetime.utcnow().isoformat(), model, init_time),
        )
        conn.commit()
        conn.close()

        return {
            "scores_saved": saved,
            "forecast_records": ingest_result.get("forecast_records", 0),
            "obs_sync": obs_sync,
        }

    except Exception as e:
        conn = get_db()
        conn.execute(
            "UPDATE model_runs SET status='error', error_message=? WHERE model=? AND init_time=?",
            (str(e), model, init_time),
        )
        conn.commit()
        conn.close()
        raise


def run_full_pipeline(progress_cb: Callable[[float, str], None] | None = None) -> dict[str, Any]:
    """Discover → register → process all pending runs."""
    def report(p: float, msg: str) -> None:
        if progress_cb:
            progress_cb(p, msg)

    report(5, "Scanning model NC files...")
    runs = discover_model_runs()
    register_runs(runs)

    pending = get_pending_runs()
    processed = 0
    errors = []

    if pending.empty:
        report(100, "Tidak ada run baru — menampilkan hasil terakhir")
        return {"discovered": len(runs), "processed": 0, "message": "Up to date", "inventory": runs}

    total = len(pending)
    for i, row in pending.iterrows():
        pct = 10 + (i / total) * 85
        report(pct, f"Verifikasi {row['model']} init {row['init_time']}...")
        try:
            process_model_run(row["model"], row["init_time"], row["nc_path"], progress_cb=report)
            processed += 1
        except Exception as e:
            errors.append({"model": row["model"], "init_time": row["init_time"], "error": str(e)})

    report(100, f"Selesai — {processed} run diproses")
    return {"discovered": len(runs), "processed": processed, "errors": errors, "inventory": runs}


def get_pipeline_status() -> dict[str, Any]:
    init_pipeline_db()
    conn = get_db()
    runs = conn.execute(
        "SELECT model, init_time, status, processed_at, nc_filename, file_size, error_message "
        "FROM model_runs ORDER BY init_time DESC LIMIT 20"
    ).fetchall()
    last_log = conn.execute(
        "SELECT * FROM pipeline_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    score_count = conn.execute("SELECT COUNT(*) FROM verification_scores").fetchone()[0]
    conn.close()
    return {
        "model_runs": [dict(r) for r in runs],
        "verification_scores_count": score_count,
        "last_pipeline": dict(last_log) if last_log else None,
        "server_paths": {m: c["path"] for m, c in MODEL_LOCAL_PATHS.items()},
        "local_nc_path": LOCAL_NC_PATH or None,
        "max_lead_time_hours": MAX_LEAD_TIME_HOURS,
    }
