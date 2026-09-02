"""Light artifact export/import for PC → webpsi daily sync.

webpsi hanya menerima skor + cache (bukan NC / forecasts penuh).
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from backend.config import ARTIFACTS_DIR, DB_PATH, MODELS


LIGHT_TABLES = (
    "model_runs",
    "verification_scores",
    "verification_station_scores",
    "ranking_cache",
    "stations",
    "station_series_cache",
)


def _connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=120)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.row_factory = sqlite3.Row
    return conn


def init_station_series_cache(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    if own:
        conn = _connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS station_series_cache (
            station_id TEXT NOT NULL,
            parameter TEXT NOT NULL,
            init_time TEXT NOT NULL,
            lead_time INTEGER NOT NULL,
            valid_time TEXT,
            obs REAL,
            InaNWP REAL, InaCAWO REAL, GFS REAL, IFS REAL,
            PRIMARY KEY (station_id, parameter, init_time, lead_time)
        );
        CREATE INDEX IF NOT EXISTS idx_station_series_lookup
            ON station_series_cache(station_id, parameter, init_time);
    """)
    if own:
        conn.commit()
        conn.close()


def rebuild_station_series_cache(
    init_times: list[str] | None = None,
    parameters: list[str] | None = None,
) -> int:
    """Precompute light station detail (obs + 4 models) dari forecasts/observations."""
    from backend.config import VERIFY_PARAMETERS
    from backend.services.obs_fetcher import load_forecasts, load_observations
    from backend.services.time_utils import normalize_valid_time

    init_station_series_cache()
    params = parameters or list(VERIFY_PARAMETERS.keys())
    obs_df = load_observations(parameters=params)
    if not obs_df.empty:
        obs_df = obs_df.copy()
        obs_df["valid_time"] = obs_df["valid_time"].map(normalize_valid_time)

    fcst_df = load_forecasts(models=MODELS, parameters=params)
    if fcst_df.empty:
        return 0
    fcst_df = fcst_df.copy()
    fcst_df["valid_time"] = fcst_df["valid_time"].map(normalize_valid_time)
    fcst_df["lead_time"] = fcst_df["lead_time"].astype(int)
    if init_times:
        fcst_df = fcst_df[fcst_df["init_time"].isin(init_times)]

    rows: list[tuple] = []
    for (station_id, parameter, init_time), grp in fcst_df.groupby(
        ["station_id", "parameter", "init_time"]
    ):
        obs_sub = obs_df[
            (obs_df["station_id"] == station_id) & (obs_df["parameter"] == parameter)
        ] if not obs_df.empty else pd.DataFrame()
        obs_map = (
            dict(zip(obs_sub["valid_time"], obs_sub["value"]))
            if not obs_sub.empty else {}
        )
        for lt, lt_grp in grp.groupby("lead_time"):
            vt = lt_grp["valid_time"].iloc[0]
            entry = {
                "station_id": station_id,
                "parameter": parameter,
                "init_time": init_time,
                "lead_time": int(lt),
                "valid_time": str(vt) if pd.notna(vt) else None,
                "obs": float(obs_map[vt]) if vt in obs_map else None,
                "InaNWP": None, "InaCAWO": None, "GFS": None, "IFS": None,
            }
            for _, r in lt_grp.iterrows():
                m = r["model"]
                if m in entry and pd.notna(r["fcst"]):
                    entry[m] = float(r["fcst"])
            rows.append((
                entry["station_id"], entry["parameter"], entry["init_time"],
                entry["lead_time"], entry["valid_time"], entry["obs"],
                entry["InaNWP"], entry["InaCAWO"], entry["GFS"], entry["IFS"],
            ))

    conn = _connect(DB_PATH)
    init_station_series_cache(conn)
    if init_times:
        for it in init_times:
            conn.execute("DELETE FROM station_series_cache WHERE init_time=?", (it,))
    else:
        conn.execute("DELETE FROM station_series_cache")
    conn.executemany(
        """INSERT OR REPLACE INTO station_series_cache
           (station_id, parameter, init_time, lead_time, valid_time, obs,
            InaNWP, InaCAWO, GFS, IFS)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    conn.close()
    return len(rows)


def load_station_series_cache(
    station_id: str,
    parameter: str,
    init_time: str | None = None,
) -> list[dict[str, Any]]:
    init_station_series_cache()
    conn = _connect(DB_PATH)
    q = """SELECT lead_time, valid_time, obs, InaNWP, InaCAWO, GFS, IFS
           FROM station_series_cache
           WHERE station_id=? AND parameter=?"""
    params: list[Any] = [station_id, parameter]
    if init_time:
        q += " AND init_time=?"
        params.append(init_time)
    q += " ORDER BY lead_time"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        # drop null model keys for cleaner payload
        entry = {
            "lead_time": d["lead_time"],
            "valid_time": d["valid_time"],
        }
        if d["obs"] is not None:
            entry["obs"] = d["obs"]
        for m in MODELS:
            if d.get(m) is not None:
                entry[m] = d[m]
        out.append(entry)
    return out


def export_light_artifacts(tag: str | None = None) -> dict[str, Any]:
    """Export skor+cache ke data/artifacts/<tag>/dashboard.sqlite (+ manifest)."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    tag = tag or datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_dir = ARTIFACTS_DIR / tag
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    # Pastikan series cache terisi sebelum export
    series_n = rebuild_station_series_cache()

    src = _connect(DB_PATH)
    # Ensure tables exist on source
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
        # Copy schema + data
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

    # Also write latest pointer
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
        "station_series_rows": series_n,
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
        # Ensure dest has table
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
