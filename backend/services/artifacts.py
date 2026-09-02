"""Light artifact export/import for PC → webpsi daily sync.

webpsi hanya menerima skor + cache (bukan NC / forecasts penuh).
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from backend.config import ARTIFACTS_DIR, DB_PATH, MODELS, OBS_FETCH_START


LIGHT_TABLES = (
    "model_runs",
    "verification_scores",
    "verification_station_scores",
    "ranking_cache",
    "stations",
    "station_calendar_cache",
)


def _connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=120)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.row_factory = sqlite3.Row
    return conn


def series_archive_start() -> datetime:
    """Awal arsip time series Detail Stasiun (default 1 Juni tahun berjalan)."""
    if OBS_FETCH_START:
        try:
            s = OBS_FETCH_START.replace("Z", "+00:00")
            return datetime.fromisoformat(s).replace(tzinfo=None)
        except ValueError:
            pass
    now = datetime.utcnow()
    return datetime(now.year, 6, 1)


def init_station_series_cache(conn: sqlite3.Connection | None = None) -> None:
    """Init calendar cache (nama fungsi lama dipertahankan untuk kompatibilitas)."""
    own = conn is None
    if own:
        conn = _connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS station_calendar_cache (
            station_id TEXT NOT NULL,
            parameter TEXT NOT NULL,
            lead_time INTEGER NOT NULL,
            valid_time TEXT NOT NULL,
            obs REAL,
            InaNWP REAL, InaCAWO REAL, GFS REAL, IFS REAL,
            PRIMARY KEY (station_id, parameter, lead_time, valid_time)
        );
        CREATE INDEX IF NOT EXISTS idx_station_calendar_lookup
            ON station_calendar_cache(station_id, parameter, lead_time, valid_time);
    """)
    if own:
        conn.commit()
        conn.close()


def rebuild_station_series_cache(
    init_times: list[str] | None = None,
    parameters: list[str] | None = None,
) -> int:
    """Precompute kalender valid_time × lead_time (semua init) untuk Detail Stasiun."""
    from backend.config import VERIFY_PARAMETERS
    from backend.services.obs_fetcher import load_forecasts, load_observations
    from backend.services.time_utils import normalize_valid_time

    init_station_series_cache()
    params = parameters or list(VERIFY_PARAMETERS.keys())
    archive_start = series_archive_start().strftime("%Y-%m-%dT%H:%M:%SZ")

    obs_df = load_observations(parameters=params, date_from=archive_start)
    if not obs_df.empty:
        obs_df = obs_df.copy()
        obs_df["valid_time"] = obs_df["valid_time"].map(normalize_valid_time)

    fcst_df = load_forecasts(models=MODELS, parameters=params)
    if fcst_df.empty and obs_df.empty:
        return 0

    if not fcst_df.empty:
        fcst_df = fcst_df.copy()
        fcst_df["valid_time"] = fcst_df["valid_time"].map(normalize_valid_time)
        fcst_df["lead_time"] = fcst_df["lead_time"].astype(int)
        if init_times:
            fcst_df = fcst_df[fcst_df["init_time"].isin(init_times)]
        fcst_df = fcst_df[fcst_df["valid_time"] >= archive_start]

    # Per lead_time: union obs times + forecast times → gaps terlihat di garis model
    obs_index: dict[tuple[str, str, str], float] = {}
    if not obs_df.empty:
        for _, r in obs_df.iterrows():
            vt = r["valid_time"]
            if not vt:
                continue
            obs_index[(str(r["station_id"]), str(r["parameter"]), str(vt))] = float(r["value"])

    lead_times = (
        sorted(int(x) for x in fcst_df["lead_time"].unique())
        if not fcst_df.empty else [0, 12]
    )
    stations_params: set[tuple[str, str]] = set()
    if not fcst_df.empty:
        for sid, param in fcst_df[["station_id", "parameter"]].drop_duplicates().itertuples(index=False):
            stations_params.add((str(sid), str(param)))
    for sid, param, _vt in obs_index:
        stations_params.add((sid, param))

    rows: list[dict] = []
    for sid, param in stations_params:
        obs_times = {vt for (s, p, vt) in obs_index if s == sid and p == param}
        for lt in lead_times:
            model_maps: dict[str, dict[str, float]] = {m: {} for m in MODELS}
            times = set(obs_times)
            if not fcst_df.empty:
                sub = fcst_df[
                    (fcst_df["station_id"].astype(str) == sid)
                    & (fcst_df["parameter"].astype(str) == param)
                    & (fcst_df["lead_time"] == lt)
                ]
                for _, r in sub.iterrows():
                    vt = str(r["valid_time"]) if pd.notna(r["valid_time"]) else None
                    if not vt:
                        continue
                    times.add(vt)
                    m = r["model"]
                    if m in model_maps and pd.notna(r["fcst"]):
                        model_maps[m][vt] = float(r["fcst"])
            for vt in times:
                rows.append({
                    "station_id": sid,
                    "parameter": param,
                    "lead_time": int(lt),
                    "valid_time": vt,
                    "obs": obs_index.get((sid, param, vt)),
                    "InaNWP": model_maps["InaNWP"].get(vt),
                    "InaCAWO": model_maps["InaCAWO"].get(vt),
                    "GFS": model_maps["GFS"].get(vt),
                    "IFS": model_maps["IFS"].get(vt),
                })

    tuples = [
        (e["station_id"], e["parameter"], e["lead_time"], e["valid_time"], e["obs"],
         e["InaNWP"], e["InaCAWO"], e["GFS"], e["IFS"])
        for e in rows
    ]

    conn = _connect(DB_PATH)
    init_station_series_cache(conn)
    conn.execute("DELETE FROM station_calendar_cache")
    conn.executemany(
        """INSERT OR REPLACE INTO station_calendar_cache
           (station_id, parameter, lead_time, valid_time, obs,
            InaNWP, InaCAWO, GFS, IFS)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        tuples,
    )
    conn.commit()
    conn.close()
    return len(tuples)


def load_station_calendar_cache(
    station_id: str,
    parameter: str,
    lead_time: int,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    init_station_series_cache()
    conn = _connect(DB_PATH)
    q = """SELECT valid_time, obs, InaNWP, InaCAWO, GFS, IFS, lead_time
           FROM station_calendar_cache
           WHERE station_id=? AND parameter=? AND lead_time=?"""
    params: list[Any] = [station_id, parameter, lead_time]
    if date_from:
        q += " AND valid_time >= ?"
        params.append(date_from)
    if date_to:
        q += " AND valid_time <= ?"
        params.append(date_to)
    q += " ORDER BY valid_time"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        entry: dict[str, Any] = {
            "valid_time": d["valid_time"],
            "lead_time": d["lead_time"],
        }
        if d["obs"] is not None:
            entry["obs"] = d["obs"]
        for m in MODELS:
            if d.get(m) is not None:
                entry[m] = d[m]
        out.append(entry)
    return out


# Alias lama
def load_station_series_cache(
    station_id: str,
    parameter: str,
    init_time: str | None = None,
) -> list[dict[str, Any]]:
    return load_station_calendar_cache(station_id, parameter, lead_time=12)


def window_bounds(months: int) -> tuple[str, str]:
    """Clamp display window ke [archive_start, now], panjang = months terakhir."""
    months = max(1, min(12, int(months)))
    now = datetime.utcnow()
    archive = series_archive_start()
    start = now - timedelta(days=30 * months)
    if start < archive:
        start = archive
    return (
        start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def build_station_calendar_live(
    station_id: str,
    parameter: str,
    model_list: list[str],
    lead_time: int,
    months: int = 3,
) -> list[dict[str, Any]]:
    """Bangun time series kalender dari forecasts+obs (PC / fallback)."""
    from backend.services.obs_fetcher import load_forecasts, load_observations
    from backend.services.time_utils import normalize_valid_time

    date_from, date_to = window_bounds(months)
    archive = series_archive_start().strftime("%Y-%m-%dT%H:%M:%SZ")
    if date_from < archive:
        date_from = archive

    obs_df = load_observations(
        parameters=[parameter], station_id=station_id,
        date_from=date_from, date_to=date_to,
    )
    if not obs_df.empty:
        obs_df = obs_df.copy()
        obs_df["valid_time"] = obs_df["valid_time"].map(normalize_valid_time)

    fcst_df = load_forecasts(
        models=model_list, parameters=[parameter],
        station_id=station_id, lead_time=lead_time,
    )
    if not fcst_df.empty:
        fcst_df = fcst_df.copy()
        fcst_df["valid_time"] = fcst_df["valid_time"].map(normalize_valid_time)
        fcst_df = fcst_df[
            (fcst_df["valid_time"] >= date_from) & (fcst_df["valid_time"] <= date_to)
        ]

    # Union of valid times
    times: set[str] = set()
    obs_map: dict[str, float] = {}
    if not obs_df.empty:
        for _, r in obs_df.iterrows():
            vt = r["valid_time"]
            if vt:
                times.add(str(vt))
                obs_map[str(vt)] = float(r["value"])

    model_maps: dict[str, dict[str, float]] = {m: {} for m in model_list}
    if not fcst_df.empty:
        for _, r in fcst_df.iterrows():
            vt = str(r["valid_time"]) if pd.notna(r["valid_time"]) else None
            if not vt:
                continue
            times.add(vt)
            m = r["model"]
            if m in model_maps and pd.notna(r["fcst"]):
                # jika beberapa init untuk valid_time sama, ambil yang terakhir (init terbaru)
                model_maps[m][vt] = float(r["fcst"])

    series: list[dict[str, Any]] = []
    for vt in sorted(times):
        entry: dict[str, Any] = {"valid_time": vt, "lead_time": lead_time}
        if vt in obs_map:
            entry["obs"] = obs_map[vt]
        for m in model_list:
            if vt in model_maps[m]:
                entry[m] = model_maps[m][vt]
        if "obs" in entry or any(m in entry for m in model_list):
            series.append(entry)
    return series


def export_light_artifacts(tag: str | None = None) -> dict[str, Any]:
    """Export skor+cache ke data/artifacts/<tag>/dashboard.sqlite (+ manifest)."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    tag = tag or datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_dir = ARTIFACTS_DIR / tag
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    series_n = rebuild_station_series_cache()

    src = _connect(DB_PATH)
    init_station_series_cache(src)
    from backend.services.verification_cache import init_verification_cache_db
    init_verification_cache_db()
    from backend.services.pipeline import init_pipeline_db
    init_pipeline_db()

    dest_path = out_dir / "dashboard.sqlite"
    dest = _connect(dest_path)

    counts: dict[str, int] = {}
    for table in LIGHT_TABLES:
        exists = src.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            counts[table] = 0
            continue
        schema = src.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()[0]
        dest.execute(schema)
        rows = src.execute(f"SELECT * FROM {table}").fetchall()
        if rows:
            cols = [d[0] for d in src.execute(f"SELECT * FROM {table} LIMIT 0").description]
            placeholders = ",".join("?" * len(cols))
            dest.executemany(
                f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
                [tuple(r) for r in rows],
            )
        counts[table] = len(rows)

    dest.commit()
    dest.close()
    src.close()

    latest = ARTIFACTS_DIR / "latest"
    if latest.exists() or latest.is_symlink():
        if latest.is_symlink() or latest.is_file():
            latest.unlink()
        else:
            shutil.rmtree(latest)
    shutil.copytree(out_dir, latest)

    manifest = {
        "tag": tag,
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "source_db": str(DB_PATH),
        "tables": counts,
        "station_calendar_rows": series_n,
        "archive_start": series_archive_start().isoformat() + "Z",
        "serve_mode": "readonly",
        "note": "Import ke webpsi: python scripts/import_artifacts.py --from data/artifacts/latest",
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (latest / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def import_light_artifacts(source: str | Path) -> dict[str, Any]:
    """Merge light tables dari artifact sqlite ke DB lokal (webpsi)."""
    src_path = Path(source)
    if src_path.is_dir():
        src_path = src_path / "dashboard.sqlite"
    if not src_path.is_file():
        raise FileNotFoundError(f"Artifact tidak ditemukan: {source}")

    from backend.services.pipeline import init_pipeline_db
    from backend.services.verification_cache import init_verification_cache_db
    from backend.services.obs_fetcher import init_db

    init_db()
    init_pipeline_db()
    init_verification_cache_db()
    init_station_series_cache()

    src = _connect(src_path)
    dest = _connect(DB_PATH)
    counts: dict[str, int] = {}

    for table in LIGHT_TABLES:
        exists = src.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            counts[table] = 0
            continue
        schema = src.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()[0]
        dest.execute(f"DROP TABLE IF EXISTS {table}")
        dest.execute(schema)
        rows = src.execute(f"SELECT * FROM {table}").fetchall()
        if rows:
            cols = [d[0] for d in src.execute(f"SELECT * FROM {table} LIMIT 0").description]
            placeholders = ",".join("?" * len(cols))
            dest.executemany(
                f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
                [tuple(r) for r in rows],
            )
        counts[table] = len(rows)

    dest.commit()
    dest.close()
    src.close()

    return {
        "imported_at": datetime.utcnow().isoformat() + "Z",
        "source": str(src_path),
        "tables": counts,
    }
