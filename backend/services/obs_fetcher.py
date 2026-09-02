"""BMKG Sinoptik API client + SFTP cache."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from backend.config import (
    DB_PATH,
    MAX_LEAD_TIME_HOURS,
    OBS_DIR,
    SFTP_HOST,
    SFTP_OBS_PATH,
    SFTP_PASSWORD,
    SFTP_PORT,
    SFTP_USER,
    SINOPTIK_PARAMETERS,
)
from backend.services.bmkg_export import fetch_sinoptik_range


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stations (
            station_id TEXT PRIMARY KEY,
            wmo_id TEXT,
            name TEXT,
            lat REAL,
            lon REAL,
            region TEXT
        );
        CREATE TABLE IF NOT EXISTS observations (
            station_id TEXT,
            valid_time TEXT,
            parameter TEXT,
            value REAL,
            unit TEXT,
            qc_flag TEXT,
            PRIMARY KEY (station_id, valid_time, parameter)
        );
        CREATE TABLE IF NOT EXISTS forecasts (
            model TEXT,
            init_time TEXT,
            lead_time INTEGER,
            station_id TEXT,
            valid_time TEXT,
            parameter TEXT,
            fcst REAL,
            PRIMARY KEY (model, init_time, lead_time, station_id, parameter)
        );
        CREATE INDEX IF NOT EXISTS idx_obs_time ON observations(valid_time);
        CREATE INDEX IF NOT EXISTS idx_fcst_model ON forecasts(model, parameter);
    """)
    conn.commit()
    conn.close()


async def fetch_and_save_sinoptik(
    date_from: str,
    date_to: str,
    station_wmo_ids: list[str] | None = None,
    parameter_names: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch all Sinoptik params via POST export API, save to DB."""
    records = await fetch_sinoptik_range(
        date_from, date_to,
        station_wmo_ids=station_wmo_ids,
        parameter_names=parameter_names or ["*"],
    )
    df = normalize_obs_records(records)
    saved = save_observations(df)
    upsert_stations_from_records(records)
    return {"fetched": len(records), "records_saved": saved}


def sync_observations_for_init(
    init_time: str | datetime,
    buffer_hours_before: int = 6,
    buffer_hours_after: int | None = None,
) -> dict[str, Any]:
    """
    Fetch BMKG Sinoptik obs covering D+0–D+7 for a model init cycle.
    Blocking wrapper — call from pipeline before verification.
    """
    if isinstance(init_time, str):
        init_dt = datetime.fromisoformat(init_time.replace("Z", ""))
    else:
        init_dt = init_time

    after = buffer_hours_after if buffer_hours_after is not None else MAX_LEAD_TIME_HOURS + 6
    date_from = (init_dt - timedelta(hours=buffer_hours_before)).strftime("%Y-%m-%dT%H:%M:%SZ")
    date_to = (init_dt + timedelta(hours=after)).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        return asyncio.run(fetch_and_save_sinoptik(date_from, date_to))
    except Exception as e:
        return {"fetched": 0, "records_saved": 0, "error": str(e)}


def sync_observations_recent(days: int = 10) -> dict[str, Any]:
    """Fetch recent observations (scheduler / cron helper)."""
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    date_from = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_to = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        return asyncio.run(fetch_and_save_sinoptik(date_from, date_to))
    except Exception as e:
        return {"fetched": 0, "records_saved": 0, "error": str(e)}


def upsert_stations_from_records(records: list[dict[str, Any]]) -> None:
    conn = get_db()
    seen = set()
    for rec in records:
        sid = str(rec.get("station_wmo_id") or rec.get("wmo_id") or rec.get("station_id") or "")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        name = rec.get("station_name") or sid
        lat = rec.get("latitude") or rec.get("lat")
        lon = rec.get("longitude") or rec.get("lon")
        conn.execute(
            "INSERT OR IGNORE INTO stations (station_id, wmo_id, name, lat, lon, region) VALUES (?,?,?,?,?,?)",
            (sid, sid, name, lat, lon, rec.get("region")),
        )
    conn.commit()
    conn.close()


def normalize_obs_records(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    numeric_params = [p for p in SINOPTIK_PARAMETERS if not p.endswith("_flag") and p not in (
        "station_name", "data_timestamp", "encoded_synop", "edited_encoded_synop",
        "created_at", "updated_at", "qc_histories", "observer_name", "land_note", "land_cond",
    )]

    for rec in records:
        station_id = str(
            rec.get("station_wmo_id") or rec.get("wmo_id") or rec.get("station_id")
            or rec.get("stationWmoId") or ""
        )
        valid_time = rec.get("data_timestamp") or rec.get("valid_time")
        if not station_id or not valid_time:
            continue

        for param in numeric_params:
            val = rec.get(param)
            if val is None or val == "" or val == "-":
                continue
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            rows.append({
                "station_id": station_id,
                "valid_time": str(valid_time),
                "parameter": param,
                "value": fval,
                "qc_flag": rec.get("qc_flag"),
                "station_name": rec.get("station_name"),
            })

    return pd.DataFrame(rows)


def save_observations(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    conn = get_db()
    count = 0
    for _, row in df.iterrows():
        conn.execute(
            """INSERT OR REPLACE INTO observations (station_id, valid_time, parameter, value, unit, qc_flag)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (row["station_id"], row["valid_time"], row["parameter"], row["value"], "", row.get("qc_flag")),
        )
        count += 1
    conn.commit()
    conn.close()
    return count


def load_observations(
    date_from: str | None = None,
    date_to: str | None = None,
    parameters: list[str] | None = None,
) -> pd.DataFrame:
    conn = get_db()
    q = "SELECT station_id, valid_time, parameter, value, qc_flag FROM observations WHERE 1=1"
    params: list[Any] = []
    if date_from:
        q += " AND valid_time >= ?"
        params.append(date_from)
    if date_to:
        q += " AND valid_time <= ?"
        params.append(date_to)
    if parameters:
        q += f" AND parameter IN ({','.join('?' * len(parameters))})"
        params.extend(parameters)
    df = pd.read_sql_query(q, conn, params=params)
    conn.close()
    return df


def save_forecasts(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    conn = get_db()
    for _, row in df.iterrows():
        conn.execute(
            """INSERT OR REPLACE INTO forecasts
               (model, init_time, lead_time, station_id, valid_time, parameter, fcst)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (row["model"], row["init_time"], row["lead_time"], row["station_id"],
             row["valid_time"], row["parameter"], row["fcst"]),
        )
    conn.commit()
    conn.close()
    return len(df)


def load_forecasts(models: list[str] | None = None, parameters: list[str] | None = None) -> pd.DataFrame:
    conn = get_db()
    q = "SELECT model, init_time, lead_time, station_id, valid_time, parameter, fcst FROM forecasts WHERE 1=1"
    params: list[Any] = []
    if models:
        q += f" AND model IN ({','.join('?' * len(models))})"
        params.extend(models)
    if parameters:
        q += f" AND parameter IN ({','.join('?' * len(parameters))})"
        params.extend(parameters)
    df = pd.read_sql_query(q, conn, params=params)
    conn.close()
    return df


def sync_obs_from_sftp() -> dict[str, Any]:
    """Download JSON obs files from SFTP monas path."""
    import paramiko

    transport = paramiko.Transport((SFTP_HOST, SFTP_PORT))
    transport.connect(username=SFTP_USER, password=SFTP_PASSWORD)
    sftp = paramiko.SFTPClient.from_transport(transport)
    downloaded = 0
    try:
        items = sftp.listdir(SFTP_OBS_PATH)
        for item in items:
            if item.endswith(".json"):
                remote = f"{SFTP_OBS_PATH.rstrip('/')}/{item}"
                local = OBS_DIR / item
                sftp.get(remote, str(local))
                with open(local) as f:
                    data = json.load(f)
                records = data if isinstance(data, list) else data.get("data", [])
                df = normalize_obs_records(records)
                downloaded += save_observations(df)
    finally:
        sftp.close()
        transport.close()
    return {"downloaded_records": downloaded, "path": SFTP_OBS_PATH}


def cache_obs_json(data: list[dict]) -> int:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    path = OBS_DIR / f"sinoptik_{ts}.json"
    with open(path, "w") as f:
        json.dump(data, f)
    df = normalize_obs_records(data)
    return save_observations(df)


def get_stations() -> pd.DataFrame:
    conn = get_db()
    df = pd.read_sql_query("SELECT * FROM stations", conn)
    conn.close()
    if df.empty:
        # Default Indonesia stations sample
        df = pd.DataFrame([
            {"station_id": "96745", "wmo_id": "96745", "name": "Citeko", "lat": -6.72, "lon": 106.85, "region": "Jakarta"},
            {"station_id": "96195", "wmo_id": "96195", "name": "Kemayoran", "lat": -6.17, "lon": 106.83, "region": "Jakarta"},
            {"station_id": "97180", "wmo_id": "97180", "name": "Juanda", "lat": -7.38, "lon": 112.78, "region": "Surabaya"},
            {"station_id": "96509", "wmo_id": "96509", "name": "Medan", "lat": 3.56, "lon": 98.67, "region": "Medan"},
            {"station_id": "97372", "wmo_id": "97372", "name": "Denpasar", "lat": -8.75, "lon": 115.17, "region": "Denpasar"},
            {"station_id": "97180", "wmo_id": "97180", "name": "Juanda", "lat": -7.38, "lon": 112.78, "region": "Surabaya"},
            {"station_id": "97724", "wmo_id": "97724", "name": "Pangkalpinang", "lat": -2.13, "lon": 106.13, "region": "Jakarta"},
            {"station_id": "97240", "wmo_id": "97240", "name": "Makassar", "lat": -5.07, "lon": 119.55, "region": "Makassar"},
            {"station_id": "97686", "wmo_id": "97686", "name": "Jayapura", "lat": -2.57, "lon": 140.49, "region": "Jayapura"},
            {"station_id": "96633", "wmo_id": "96633", "name": "Padang", "lat": -0.88, "lon": 100.35, "region": "Medan"},
        ])
        conn = get_db()
        for _, row in df.drop_duplicates("station_id").iterrows():
            conn.execute(
                "INSERT OR IGNORE INTO stations VALUES (?,?,?,?,?,?)",
                (row["station_id"], row["wmo_id"], row["name"], row["lat"], row["lon"], row["region"]),
            )
        conn.commit()
        conn.close()
    return df.drop_duplicates("station_id")
