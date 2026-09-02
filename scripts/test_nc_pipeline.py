#!/usr/bin/env python3
r"""
Test pipeline NC lokal (12GB+) tanpa menjalankan full dashboard.

Contoh (PC BMKG / Windows):
  set LOCAL_NC_PATH=C:/Users/husei/Downloads/2026070112-d01-asim.nc
  python scripts/test_nc_pipeline.py --inspect
  python scripts/test_nc_pipeline.py --ingest-only
  python scripts/test_nc_pipeline.py --full

Contoh (Linux):
  LOCAL_NC_PATH=/data/2026070112-d01-asim.nc python scripts/test_nc_pipeline.py --full
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("PYTHONPATH", str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def _resolve_nc_path(cli_path: str | None) -> Path:
    raw = cli_path or os.getenv("LOCAL_NC_PATH", "")
    if not raw:
        print("ERROR: Set LOCAL_NC_PATH di .env atau gunakan --nc-path")
        sys.exit(1)
    p = Path(raw.strip().strip('"').strip("'"))
    if not p.is_file():
        print(f"ERROR: File tidak ditemukan: {p}")
        sys.exit(1)
    return p


def cmd_inspect(nc_path: Path) -> None:
    from backend.services.nc_reader import inspect_nc

    print(f"File: {nc_path}")
    print(f"Size: {nc_path.stat().st_size / 1e9:.2f} GB")
    info = inspect_nc(nc_path)
    for k, v in info.items():
        print(f"  {k}: {v}")


def cmd_ingest(nc_path: Path, model: str) -> None:
    from backend.services.nc_ingest import ingest_nc_from_path
    from backend.services.obs_fetcher import init_db

    init_db()
    t0 = time.time()

    def progress(p: float, msg: str) -> None:
        print(f"  [{p:5.1f}%] {msg}")

    result = ingest_nc_from_path(nc_path, model, copy_to_data_dir=False, progress_cb=progress)
    elapsed = time.time() - t0
    print(f"\nSelesai dalam {elapsed / 60:.1f} menit")
    print(f"  forecast_records: {result.get('forecast_records')}")
    print(f"  stations: {result.get('stations')}")


def cmd_full(nc_path: Path, model: str, fetch_obs: bool, fresh: bool) -> None:
    from backend.services.nc_reader import parse_init_time
    from backend.services.obs_fetcher import init_db, sync_observations_for_init
    from backend.services.pipeline import init_pipeline_db, process_model_run, register_runs
    from backend.services.station_catalog import catalog_to_dataframe

    init_db()
    init_pipeline_db()

    cat = catalog_to_dataframe()
    print(f"  katalog stasiun: {len(cat)} stasiun (WMO + lat/lon)")

    init_time = parse_init_time(nc_path.name)
    if not init_time:
        print("ERROR: Tidak bisa parse init time dari nama file")
        sys.exit(1)

    register_runs([{
        "model": model,
        "init_time": init_time.isoformat(),
        "nc_filename": nc_path.name,
        "nc_path": str(nc_path.resolve()),
        "file_size": nc_path.stat().st_size,
    }])

    if fetch_obs:
        print(f"Fetch observasi BMKG untuk init {init_time.isoformat()}...")
        obs_result = sync_observations_for_init(init_time.isoformat())
        print(f"  obs: {obs_result}")
    else:
        from backend.config import OBS_EXPORT_DIR
        from backend.services.obs_sync import import_obs_from_json_dir
        imported = import_obs_from_json_dir(OBS_EXPORT_DIR, clear_existing=fresh)
        print(f"  import obs JSON: {imported}")

    t0 = time.time()

    def progress(p: float, msg: str) -> None:
        print(f"  [{p:5.1f}%] {msg}")

    result = process_model_run(model, init_time.isoformat(), str(nc_path), progress_cb=progress)
    elapsed = time.time() - t0
    print(f"\nPipeline selesai dalam {elapsed / 60:.1f} menit")
    print(f"  scores_saved: {result.get('scores_saved')}")
    print(f"  forecast_records: {result.get('forecast_records')}")
    if result.get("obs_sync"):
        print(f"  obs_sync: {result['obs_sync']}")
    if result.get("dummy_models"):
        print(f"  dummy_models: {result['dummy_models']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test NWP NC pipeline lokal")
    parser.add_argument("--nc-path", help="Path ke file .nc (override LOCAL_NC_PATH)")
    parser.add_argument("--model", default="InaNWP", choices=["InaNWP", "InaCAWO", "GFS", "IFS"])
    parser.add_argument("--inspect", action="store_true", help="Inspect variabel NC saja")
    parser.add_argument("--ingest-only", action="store_true", help="Hanya baca NC → forecast DB")
    parser.add_argument("--full", action="store_true", help="Pipeline lengkap: NC + obs + HARP")
    parser.add_argument("--no-fetch-obs", action="store_true", help="Skip fetch BMKG API")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Hapus obs/forecast/skor lama sebelum import (wajib setelah update katalog WMO)",
    )
    args = parser.parse_args()

    nc_path = _resolve_nc_path(args.nc_path)

    if args.inspect:
        cmd_inspect(nc_path)
    elif args.ingest_only:
        cmd_ingest(nc_path, args.model)
    elif args.full:
        cmd_full(nc_path, args.model, fetch_obs=not args.no_fetch_obs, fresh=args.fresh)
    else:
        cmd_inspect(nc_path)
        print("\nGunakan --ingest-only atau --full untuk lanjut pipeline")


if __name__ == "__main__":
    main()
