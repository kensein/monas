"""Process large NetCDF files from local path (supports 11GB+)."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pandas as pd

from backend.config import NC_DIR, VERIFY_PARAMETERS
from backend.services.nc_reader import inspect_nc, read_point_forecast
from backend.services.obs_fetcher import get_stations, save_forecasts


def resolve_local_path(path_str: str) -> Path:
    """Resolve Windows or Unix path."""
    p = Path(path_str.strip().strip('"').strip("'"))
    if not p.is_absolute():
        raise FileNotFoundError(f"Path harus absolute: {path_str}")
    if not p.exists():
        raise FileNotFoundError(f"File tidak ditemukan: {p}")
    if p.suffix.lower() != ".nc":
        raise ValueError("File harus berformat .nc")
    return p


def ingest_nc_from_path(
    path: Path,
    model: str,
    copy_to_data_dir: bool = False,
    progress_cb: Callable[[float, str], None] | None = None,
) -> dict:
    """Read NC from local disk and extract point forecasts."""
    def report(p: float, msg: str) -> None:
        if progress_cb:
            progress_cb(p, msg)

    report(5, f"Memeriksa file: {path.name} ({path.stat().st_size / 1e9:.2f} GB)")

    nc_path = path
    if copy_to_data_dir:
        dest = NC_DIR / path.name
        if not dest.exists() or dest.stat().st_size != path.stat().st_size:
            report(10, "Menyalin file ke data/nc (opsional)...")
            import shutil
            shutil.copy2(path, dest)
        nc_path = dest

    report(15, "Inspect variabel NetCDF...")
    info = inspect_nc(nc_path)

    report(25, "Memuat metadata stasiun...")
    stations = get_stations()
    params = list(VERIFY_PARAMETERS.keys())

    report(40, "Interpolasi grid → titik stasiun (lazy read)...")

    def _ingest_progress(p: float, msg: str) -> None:
        mapped = 40 + max(0.0, min(p, 78.0) - 40.0) / 38.0 * 39.0
        report(mapped, msg)

    fcst_df = read_point_forecast(
        nc_path, model, stations, params, progress_cb=_ingest_progress,
    )

    report(80, f"Menyimpan {len(fcst_df)} records forecast...")
    saved = save_forecasts(fcst_df)

    report(100, "Selesai")
    return {
        "path": str(path),
        "filename": path.name,
        "size_gb": round(path.stat().st_size / 1e9, 2),
        "info": info,
        "forecast_records": saved,
        "model": model,
        "stations": int(fcst_df["station_id"].nunique()) if not fcst_df.empty else 0,
    }
