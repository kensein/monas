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
    PARALLEL_BACKEND,
    PARALLEL_VERIFY,
    PARALLEL_WORKERS,
    USE_DUMMY_MODELS,
)
from backend.services.nc_ingest import ingest_nc_from_path
from backend.services.nc_reader import parse_init_time
from backend.services.obs_fetcher import get_stations, load_observations, sync_observations_for_init
from backend.services.sftp_client import list_remote_nc_files, resolve_model_nc_path


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
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
    try:
        conn.execute("ALTER TABLE model_runs ADD COLUMN data_source TEXT DEFAULT 'real'")
    except sqlite3.OperationalError:
        pass
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
    # Job yang di-kill sering tertinggal status=processing
    conn.execute(
        "UPDATE model_runs SET status='pending' WHERE status='processing'"
    )
    conn.commit()
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
    from backend.services.verification_cache import refresh_ranking_cache
    refresh_ranking_cache()
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
    """Fetch/import observasi untuk init cycle. litbangweb: dari JSON lokal; PC/webpsi: BMKG API."""
    from backend.config import LITBANGWEB_OBS_DIR, OFFLINE_OBS_MODE
    from backend.services.obs_sync import import_obs_from_json_dir

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

    # litbangweb offline: import JSON yang di-upload dari PC lokal
    if OFFLINE_OBS_MODE or Path(LITBANGWEB_OBS_DIR).is_dir():
        if progress_cb:
            progress_cb(55, f"Import observasi JSON dari {LITBANGWEB_OBS_DIR}...")
        imported = import_obs_from_json_dir(LITBANGWEB_OBS_DIR)
        obs_df = load_observations()
        if not obs_df.empty:
            return {"source": "json_dir", **imported}
        if OFFLINE_OBS_MODE:
            raise ValueError(
                f"OFFLINE_OBS_MODE: tidak ada observasi di {LITBANGWEB_OBS_DIR}. "
                "Jalankan fetch_obs_local.py --sync di PC lokal."
            )

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
        print(f"[pipeline] {model} {init_time}: [{p:5.1f}%] {msg}", flush=True)
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
        report(10, f"Membaca NC: {local_path.name} ({local_path.stat().st_size / 1e6:.0f} MB)")

        ingest_result = ingest_nc_from_path(local_path, model, copy_to_data_dir=False, progress_cb=report)

        obs_sync = _ensure_observations(init_time, progress_cb)
        report(70, "Menghitung skor verifikasi HARP D+0–D+7...")
        obs_df = load_observations()
        if obs_df.empty:
            raise ValueError("Data observasi kosong — jalankan sync observasi terlebih dahulu")

        from backend.services.verification_cache import (
            compute_run_verification,
            refresh_ranking_cache,
            save_verification_station_scores,
        )
        all_scores, station_rows = compute_run_verification(model, init_time, obs_df)
        saved = save_verification_scores(all_scores)
        save_verification_station_scores(station_rows)
        refresh_ranking_cache()
        report(88, f"Skor tersimpan: {saved} agregat, {len(station_rows)} stasiun")

        conn = get_db()
        conn.execute(
            """UPDATE model_runs SET status='done', processed_at=?, error_message=NULL, data_source='real'
               WHERE model=? AND init_time=?""",
            (datetime.utcnow().isoformat(), model, init_time),
        )
        conn.commit()
        conn.close()

        result = {
            "scores_saved": saved,
            "forecast_records": ingest_result.get("forecast_records", 0),
            "obs_sync": obs_sync,
        }

        if model == "InaNWP" and USE_DUMMY_MODELS:
            from backend.services.dummy_models import verify_dummy_models
            report(90, "Generate & verifikasi model dummy (InaCAWO, GFS, IFS)...")
            dummy_result = verify_dummy_models(init_time, progress_cb=progress_cb)
            result["dummy_models"] = dummy_result

        return result

    except Exception as e:
        conn = get_db()
        conn.execute(
            "UPDATE model_runs SET status='error', error_message=? WHERE model=? AND init_time=?",
            (str(e), model, init_time),
        )
        conn.commit()
        conn.close()
        raise


def _process_run_worker(model: str, init_time: str, nc_path: str) -> dict[str, Any]:
    """Top-level worker for ProcessPoolExecutor (picklable)."""
    try:
        result = process_model_run(model, init_time, nc_path)
        return {"ok": True, "model": model, "init_time": init_time, "result": result}
    except Exception as e:
        return {"ok": False, "model": model, "init_time": init_time, "error": str(e)}


def run_full_pipeline(
    progress_cb: Callable[[float, str], None] | None = None,
    parallel: bool | None = None,
) -> dict[str, Any]:
    """Discover → register → process pending runs (opsional paralel per model)."""
    import os

    def report(p: float, msg: str) -> None:
        line = f"[pipeline] [{p:5.1f}%] {msg}"
        print(line, flush=True)
        if progress_cb:
            progress_cb(p, msg)

    report(5, "Scanning model NC files...")
    runs = discover_model_runs()
    register_runs(runs)

    pending = get_pending_runs()
    processed = 0
    errors: list[dict[str, Any]] = []

    if pending.empty:
        report(100, "Tidak ada run baru — menampilkan hasil terakhir")
        return {"discovered": len(runs), "processed": 0, "message": "Up to date", "inventory": runs}

    max_runs = int(os.getenv("VERIFY_MAX_RUNS", "0") or "0")
    if max_runs > 0 and len(pending) > max_runs:
        report(8, f"VERIFY_MAX_RUNS={max_runs}: proses {max_runs}/{len(pending)} run terbaru")
        pending = pending.head(max_runs)

    use_parallel = PARALLEL_VERIFY if parallel is None else parallel
    jobs = [
        (row["model"], row["init_time"], row["nc_path"])
        for _, row in pending.iterrows()
    ]
    total = len(jobs)

    if use_parallel and total > 1:
        report(10, f"Verifikasi paralel {total} run (workers={PARALLEL_WORKERS}, backend={PARALLEL_BACKEND})...")
        from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed

        Executor = ThreadPoolExecutor if PARALLEL_BACKEND == "thread" else ProcessPoolExecutor
        done = 0
        with Executor(max_workers=min(PARALLEL_WORKERS, total)) as ex:
            futs = {
                ex.submit(_process_run_worker, m, it, p): (m, it)
                for m, it, p in jobs
            }
            for fut in as_completed(futs):
                out = fut.result()
                done += 1
                pct = 10 + (done / total) * 85
                if out.get("ok"):
                    processed += 1
                    report(pct, f"OK {out['model']} {out['init_time']} ({done}/{total})")
                else:
                    errors.append({
                        "model": out["model"],
                        "init_time": out["init_time"],
                        "error": out.get("error", "unknown"),
                    })
                    report(pct, f"ERR {out['model']}: {out.get('error')}")
    else:
        for i, (model, init_time, nc_path) in enumerate(jobs):
            pct = 10 + (i / total) * 85
            report(pct, f"Verifikasi {model} init {init_time} ({i + 1}/{total})...")
            try:
                process_model_run(model, init_time, nc_path, progress_cb=report)
                processed += 1
            except Exception as e:
                errors.append({"model": model, "init_time": init_time, "error": str(e)})
                report(pct, f"ERR {model} {init_time}: {e}")

    report(100, f"Selesai — {processed} run diproses ({'paralel' if use_parallel and total > 1 else 'serial'})")
    return {
        "discovered": len(runs),
        "processed": processed,
        "errors": errors,
        "inventory": runs,
        "parallel": use_parallel and total > 1,
    }


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
        "model_sources": __import__("backend.services.dummy_models", fromlist=["get_model_data_sources"]).get_model_data_sources(),
    }
