#!/usr/bin/env python3
r"""
Download observasi Sinoptik BMKG API v21 → simpan JSON → (opsional) upload ke litbangweb.

Sesuai API Export Sinoptik (search api).pptx:
  POST /api/v21/user/session/login          (token 48 jam, auto-refresh di app)
  POST /api/v21/export/observation/by-station/query
  Body: data_type=sinoptik, parameter_names=["*"], station_wmo_ids=["*"]
  Chunk ≤4 hari per request

Contoh (PC BMKG):
  python scripts\fetch_obs_local.py --days 10
  python scripts\fetch_obs_local.py --days 10 --sync
  python scripts\fetch_obs_local.py --from 2026-07-01 --to 2026-07-10 --sync

File tersimpan di OBS_EXPORT_DIR (default D:\nwp-data\obs).
Upload ke litbangweb: SFTP port 3346 → /opt/lampp/htdocs/monas/obs/
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch observasi BMKG → JSON → litbangweb")
    parser.add_argument("--days", type=int, default=10, help="Hari terakhir (default 10)")
    parser.add_argument("--from", dest="date_from", help="ISO date_from, e.g. 2026-07-01T00:00:00Z")
    parser.add_argument("--to", dest="date_to", help="ISO date_to")
    parser.add_argument("--sync", action="store_true", help="Upload ke litbangweb setelah download")
    parser.add_argument("--upload-only", action="store_true", help="Hanya upload JSON yang sudah ada")
    parser.add_argument("--import-db", action="store_true", help="Simpan juga ke SQLite lokal")
    args = parser.parse_args()

    from backend.config import OBS_EXPORT_DIR
    from backend.services.obs_sync import (
        fetch_and_export_sinoptik,
        upload_obs_exports_to_litbangweb,
    )

    print(f"OBS_EXPORT_DIR: {OBS_EXPORT_DIR}")

    if args.upload_only:
        result = upload_obs_exports_to_litbangweb()
        print(f"Upload: {result}")
        return

    if args.date_from and args.date_to:
        date_from, date_to = args.date_from, args.date_to
    else:
        end = datetime.utcnow()
        start = end - timedelta(days=args.days)
        date_from = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        date_to = end.strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"Fetch {date_from} → {date_to} ...")
    result = asyncio.run(
        fetch_and_export_sinoptik(date_from, date_to, also_save_db=args.import_db)
    )
    print(f"  file: {result['file']}")
    print(f"  records: {result['records']}")

    if args.sync:
        print("Upload ke litbangweb SFTP...")
        up = upload_obs_exports_to_litbangweb()
        print(f"  uploaded: {up['uploaded']} file(s) → {up.get('remote_path')}")
        for f in up.get("files", []):
            print(f"    {f['local']} ({f['size']:,} bytes)")


if __name__ == "__main__":
    main()
