"""BMKG Sinoptik API client + SFTP cache."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from backend.config import (
    BMKG_API_BASE,
    BMKG_PASSWORD,
    BMKG_USERNAME,
    DB_PATH,
    OBS_DIR,
    SFTP_HOST,
    SFTP_OBS_PATH,
    SFTP_PASSWORD,
    SFTP_PORT,
    SFTP_USER,
    SINOPTIK_PARAMETERS,
)


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


class BMKGClient:
    def __init__(self) -> None:
        self.base = BMKG_API_BASE.rstrip("/")
        self.token: str | None = None

    async def login(self) -> str:
        if not BMKG_USERNAME or not BMKG_PASSWORD:
            raise ValueError("BMKG_USERNAME dan BMKG_PASSWORD belum diset di environment")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base}/api/v21/user/session/login",
                json={"username": BMKG_USERNAME, "password": BMKG_PASSWORD},
            )
            resp.raise_for_status()
            data = resp.json()
            self.token = data.get("token") or data.get("access_token") or data.get("data", {}).get("token")
            if not self.token:
                raise ValueError(f"Token tidak ditemukan dalam response: {list(data.keys())}")
            return self.token

    async def fetch_sinoptik(
        self,
        date_from: str,
        date_to: str,
        station_wmo_ids: list[str] | None = None,
        parameter_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.token:
            await self.login()

        body = {
            "data_type": "sinoptik",
            "parameter_names": parameter_names or ["*"],
            "station_wmo_ids": station_wmo_ids or ["*"],
            "date_from": date_from,
            "date_to": date_to,
            "order_timestamp_code": 1,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.base}/api/v21/export/observation/by-station/query",
                headers={"Authorization": f"Bearer {self.token}"},
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            return data.get("data") or data.get("results") or data.get("items") or []


def normalize_obs_records(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    numeric_params = [p for p in SINOPTIK_PARAMETERS if not p.endswith("_flag") and p not in (
        "station_name", "data_timestamp", "encoded_synop", "edited_encoded_synop",
        "created_at", "updated_at", "qc_histories", "observer_name", "land_note", "land_cond",
    )]

    for rec in records:
        station_id = str(rec.get("station_wmo_id") or rec.get("wmo_id") or rec.get("station_id", ""))
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
