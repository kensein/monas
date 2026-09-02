"""Pre-computed dashboard outputs (PSIIDN-style): store at pipeline time, serve from SQLite."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from backend.config import MAX_LEAD_TIME_HOURS, MODELS, VERIFY_PARAMETERS
from backend.services.obs_fetcher import get_db, load_forecasts, load_observations
from backend.services.verification import VerificationResult, build_verification_pairs, compute_ranking, det_verify


def init_verification_cache_db() -> None:
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS verification_station_scores (
            model TEXT NOT NULL,
            parameter TEXT NOT NULL,
            init_time TEXT NOT NULL,
            lead_time INTEGER NOT NULL,
            station_id TEXT NOT NULL,
            bias REAL, rmse REAL, mae REAL,
            n_cases INTEGER,
            obs_mean REAL, fcst_mean REAL,
            computed_at TEXT,
            PRIMARY KEY (model, parameter, init_time, lead_time, station_id)
        );
        CREATE INDEX IF NOT EXISTS idx_station_scores_lookup
            ON verification_station_scores(model, parameter, init_time, lead_time);
        CREATE TABLE IF NOT EXISTS ranking_cache (
            init_time TEXT NOT NULL DEFAULT '',
            models_key TEXT NOT NULL,
            score_metric TEXT NOT NULL DEFAULT 'rmse',
            payload TEXT NOT NULL,
            computed_at TEXT,
            PRIMARY KEY (init_time, models_key, score_metric)
        );
    """)
    conn.commit()
    conn.close()


def station_scores_from_pairs(pairs: pd.DataFrame) -> pd.DataFrame:
    if pairs.empty:
        return pd.DataFrame()
    df = pairs.copy()
    df["error"] = df["fcst"] - df["obs"]
    return df.groupby("station_id").agg(
        rmse=("error", lambda e: float((e ** 2).mean()) ** 0.5),
        bias=("error", "mean"),
        mae=("error", lambda e: float(np.abs(e).mean())),
        n_cases=("fcst", "count"),
        obs_mean=("obs", "mean"),
        fcst_mean=("fcst", "mean"),
    ).reset_index()


def compute_run_verification(
    model: str,
    init_time: str,
    obs_df: pd.DataFrame | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Hitung skor agregat + per-stasiun untuk satu model/init (dipanggil saat pipeline)."""
    if obs_df is None:
        obs_df = load_observations()
    all_scores: list[dict[str, Any]] = []
    station_rows: list[dict[str, Any]] = []

    for param, meta in VERIFY_PARAMETERS.items():
        circular = meta.get("category") == "circular"
        param_obs = obs_df[obs_df["parameter"] == param]
        if param_obs.empty:
            continue

        fcst_df = load_forecasts(models=[model], parameters=[param])
        fcst_df = fcst_df[fcst_df["init_time"] == init_time]
        fcst_df = fcst_df[fcst_df["lead_time"] <= MAX_LEAD_TIME_HOURS]
        if fcst_df.empty:
            continue

        for lt in sorted(fcst_df["lead_time"].unique()):
            lt_fcst = fcst_df[fcst_df["lead_time"] == lt]
            pairs = build_verification_pairs(param_obs, lt_fcst, param, circular=circular)
            vr = det_verify(pairs, param, model, int(lt), circular=circular)
            if vr:
                d = vr.to_dict()
                d["init_time"] = init_time
                all_scores.append(d)

            for _, row in station_scores_from_pairs(pairs).iterrows():
                station_rows.append({
                    "model": model,
                    "parameter": param,
                    "init_time": init_time,
                    "lead_time": int(lt),
                    "station_id": row["station_id"],
                    "bias": float(row["bias"]),
                    "rmse": float(row["rmse"]),
                    "mae": float(row["mae"]),
                    "n_cases": int(row["n_cases"]),
                    "obs_mean": float(row["obs_mean"]),
                    "fcst_mean": float(row["fcst_mean"]),
                })

    return all_scores, station_rows


def save_verification_station_scores(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    init_verification_cache_db()
    conn = get_db()
    ts = datetime.utcnow().isoformat()
    for r in rows:
        conn.execute(
            """INSERT OR REPLACE INTO verification_station_scores
               (model, parameter, init_time, lead_time, station_id,
                bias, rmse, mae, n_cases, obs_mean, fcst_mean, computed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                r["model"], r["parameter"], r["init_time"], r["lead_time"], r["station_id"],
                r.get("bias"), r.get("rmse"), r.get("mae"), r.get("n_cases"),
                r.get("obs_mean"), r.get("fcst_mean"), ts,
            ),
        )
    conn.commit()
    conn.close()
    return len(rows)


def load_verification_station_scores(
    model: str,
    parameter: str,
    lead_time: int,
    init_time: str | None = None,
) -> pd.DataFrame:
    init_verification_cache_db()
    conn = get_db()
    q = """SELECT station_id, bias, rmse, mae, n_cases, obs_mean, fcst_mean
           FROM verification_station_scores
           WHERE model=? AND parameter=? AND lead_time=?"""
    params: list[Any] = [model, parameter, lead_time]
    if init_time:
        q += " AND init_time=?"
        params.append(init_time)
    df = pd.read_sql_query(q, conn, params=params)
    conn.close()
    return df


def load_verification_station_scores_bulk(
    model: str,
    parameter: str,
    init_time: str | None = None,
) -> pd.DataFrame:
    """All lead times for one model/parameter — served once, filtered di frontend."""
    init_verification_cache_db()
    conn = get_db()
    q = """SELECT lead_time, station_id, bias, rmse, mae, n_cases, obs_mean, fcst_mean
           FROM verification_station_scores
           WHERE model=? AND parameter=?"""
    params: list[Any] = [model, parameter]
    if init_time:
        q += " AND init_time=?"
        params.append(init_time)
    q += " ORDER BY lead_time, station_id"
    df = pd.read_sql_query(q, conn, params=params)
    conn.close()
    return df


def _models_key(models: list[str]) -> str:
    return ",".join(sorted(m.strip() for m in models if m.strip()))


def _ranking_payload(models: list[str], init_time: str | None, score: str) -> dict[str, Any]:
    from backend.services.pipeline import load_verification_scores

    df = load_verification_scores(models=models, init_time=init_time)
    if df.empty:
        return {}
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


def refresh_ranking_cache(models: list[str] | None = None) -> int:
    """Pre-compute ranking untuk semua init cycle + agregat (pola PSIIDN)."""
    from backend.services.pipeline import load_verification_scores

    init_verification_cache_db()
    models = models or MODELS
    df = load_verification_scores(models=models)
    if df.empty:
        return 0

    init_times = [None] + sorted(df["init_time"].dropna().unique().tolist())
    ts = datetime.utcnow().isoformat()
    conn = get_db()
    n = 0
    for init_time in init_times:
        payload = _ranking_payload(models, init_time, "rmse")
        if not payload:
            continue
        key = init_time or ""
        conn.execute(
            """INSERT OR REPLACE INTO ranking_cache
               (init_time, models_key, score_metric, payload, computed_at)
               VALUES (?,?,?,?,?)""",
            (key, _models_key(models), "rmse", json.dumps(payload), ts),
        )
        n += 1
    conn.commit()
    conn.close()
    return n


def load_ranking_cache(
    models: list[str],
    init_time: str | None = None,
    score: str = "rmse",
) -> dict[str, Any] | None:
    init_verification_cache_db()
    conn = get_db()
    row = conn.execute(
        """SELECT payload FROM ranking_cache
           WHERE init_time=? AND models_key=? AND score_metric=?""",
        (init_time or "", _models_key(models), score),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return json.loads(row[0])


def get_or_build_ranking(
    models: list[str],
    init_time: str | None = None,
    score: str = "rmse",
) -> dict[str, Any]:
    cached = load_ranking_cache(models, init_time, score)
    if cached:
        return cached
    payload = _ranking_payload(models, init_time, score)
    if not payload:
        return {}
    init_verification_cache_db()
    conn = get_db()
    conn.execute(
        """INSERT OR REPLACE INTO ranking_cache
           (init_time, models_key, score_metric, payload, computed_at)
           VALUES (?,?,?,?,?)""",
        (init_time or "", _models_key(models), score, json.dumps(payload), datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    return payload


def rebuild_dashboard_cache() -> dict[str, int]:
    """Backfill cache stasiun + ranking dari data forecast/obs yang sudah ada."""
    from backend.services.pipeline import get_available_cycles

    init_verification_cache_db()
    obs_df = load_observations()
    station_total = 0
    score_total = 0

    cycles = get_available_cycles()
    done = [c for c in cycles if c.get("status") == "done"]
    seen: set[tuple[str, str]] = set()
    for c in done:
        key = (c["model"], c["init_time"])
        if key in seen:
            continue
        seen.add(key)
        scores, station_rows = compute_run_verification(c["model"], c["init_time"], obs_df)
        if station_rows:
            station_total += save_verification_station_scores(station_rows)
        score_total += len(scores)

    ranking_total = refresh_ranking_cache()
    return {
        "station_rows": station_total,
        "score_rows": score_total,
        "ranking_keys": ranking_total,
    }


def cache_is_stale() -> bool:
    """True jika ada skor agregat tapi cache peta/ranking belum terisi."""
    init_verification_cache_db()
    conn = get_db()
    scores = conn.execute("SELECT COUNT(*) FROM verification_scores").fetchone()[0]
    stations = conn.execute("SELECT COUNT(*) FROM verification_station_scores").fetchone()[0]
    rankings = conn.execute("SELECT COUNT(*) FROM ranking_cache").fetchone()[0]
    conn.close()
    return scores > 0 and (stations == 0 or rankings == 0)
