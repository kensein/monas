#!/usr/bin/env python3
r"""
Download observasi Sinoptik BMKG API v21 → simpan JSON → (opsional) upload ke litbangweb.

Contoh (PC BMKG):
  python scripts\fetch_obs_local.py --test-api
  python scripts\fetch_obs_local.py --days 10
  python scripts\fetch_obs_local.py --from 2026-07-01T00:00:00Z --to 2026-07-05T23:59:00Z --sync
"""
from __future__ import annotations

import argparse
import asyncio
import json
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


def _print_diag(d: dict) -> None:
    print(json.dumps(d, indent=2, ensure_ascii=False, default=str))


async def _run_test_api(date_from: str, date_to: str) -> None:
    from backend.config import BMKG_USERNAME, BMKG_PASSWORD, BMKG_API_BASE
    from backend.services.bmkg_auth import token_status
    from backend.services.bmkg_export import diagnose_sinoptik_api

    print("=== Diagnostik BMKG API ===")
    print(f"  API base : {BMKG_API_BASE}")
    print(f"  Username : {BMKG_USERNAME or '(KOSONG!)'}")
    print(f"  Password : {'***' if BMKG_PASSWORD else '(KOSONG!)'}")
    print(f"  Rentang  : {date_from} → {date_to}")
    print()

    diag = await diagnose_sinoptik_api(date_from, date_to)
    _print_diag(diag)
    print()
    print("Token cache:", token_status())

    if diag.get("records_parsed", 0) == 0:
        print()
        print("TIPS jika 0 records:")
        print("  1. Cek BMKG_PASSWORD di .env")
        print("  2. Coba rentang saat NC init (contoh Juli 2026):")
        print('     python scripts\\fetch_obs_local.py --from 2026-07-01T00:00:00Z --to 2026-07-05T23:59:00Z')
        print("  3. Pastikan PC di intranet BMKG (bukan internet publik)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch observasi BMKG → JSON → litbangweb")
    parser.add_argument("--days", type=int, default=10, help="Hari terakhir (default 10)")
    parser.add_argument("--from", dest="date_from", help="ISO date_from")
    parser.add_argument("--to", dest="date_to", help="ISO date_to")
    parser.add_argument("--sync", action="store_true", help="Upload ke litbangweb setelah download")
    parser.add_argument("--upload-only", action="store_true", help="Hanya upload JSON yang sudah ada")
    parser.add_argument("--import-db", action="store_true", help="Simpan juga ke SQLite lokal")
    parser.add_argument("--inspect-obs", metavar="FILE", help="Inspect struktur JSON observasi lokal")
    parser.add_argument("--test-sftp", action="store_true", help="Test buat folder + write ke litbangweb")
    args = parser.parse_args()

    from backend.config import OBS_EXPORT_DIR, SFTP_OBS_PATH
    from backend.services.obs_sync import (
        SFTP_OBS_FALLBACK,
        fetch_and_export_sinoptik,
        upload_obs_exports_to_litbangweb,
        _connect_sftp,
        _sftp_makedirs,
    )

    print(f"OBS_EXPORT_DIR : {OBS_EXPORT_DIR}")
    print(f"SFTP_OBS_PATH  : {SFTP_OBS_PATH}")
    print(f"SFTP fallback  : {SFTP_OBS_FALLBACK}")
    print()

    if args.test_sftp:
        print("=== Test SFTP litbangweb ===")
        sftp, transport = _connect_sftp()
        for path in (SFTP_OBS_PATH, SFTP_OBS_FALLBACK):
            try:
                _sftp_makedirs(sftp, path)
                test_file = f"{path}/.monas_write_test"
                with sftp.file(test_file, "w") as f:
                    f.write("ok")
                sftp.remove(test_file)
                print(f"  OK writable: {path}")
            except OSError as e:
                print(f"  GAGAL {path}: {e}")
        sftp.close()
        transport.close()
        return

    if args.date_from and args.date_to:
        date_from, date_to = args.date_from, args.date_to
    else:
        end = datetime.utcnow()
        start = end - timedelta(days=args.days)
        date_from = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        date_to = end.strftime("%Y-%m-%dT%H:%M:%SZ")

    if args.inspect_obs:
        import json
        from backend.services.obs_format import flatten_sinoptik_records
        from backend.services.obs_fetcher import normalize_obs_records
        p = Path(args.inspect_obs)
        raw = json.loads(p.read_text(encoding="utf-8"))
        data = raw.get("data", raw) if isinstance(raw, dict) else raw
        print(f"File: {p}")
        print(f"Top type: {type(raw).__name__}, data len: {len(data) if isinstance(data, list) else 'n/a'}")
        if isinstance(data, list) and data:
            print(f"First item type: {type(data[0]).__name__}")
            if isinstance(data[0], dict):
                print(f"First keys: {list(data[0].keys())[:15]}")
            elif isinstance(data[0], list):
                print(f"First row len: {len(data[0])}, sample: {data[0][:5]}")
        flat = flatten_sinoptik_records(raw)
        df = normalize_obs_records(raw)
        print(f"Flattened records: {len(flat)}")
        print(f"DB rows: {len(df)}")
        if flat and isinstance(flat[0], dict):
            print(f"Flat[0] keys: {list(flat[0].keys())[:15]}")
        return

    if args.test_api:
        asyncio.run(_run_test_api(date_from, date_to))
        return

    if args.upload_only:
        result = upload_obs_exports_to_litbangweb(min_records=0)
        print(f"Upload: {result}")
        return

    print(f"Fetch {date_from} → {date_to} ...")
    result = asyncio.run(
        fetch_and_export_sinoptik(date_from, date_to, also_save_db=args.import_db)
    )
    print(f"  file    : {result['file']}")
    print(f"  records : {result['records']}")
    if result.get("warning"):
        print(f"  WARNING : {result['warning']}")

    if args.sync:
        if result["records"] == 0:
            print("Upload dilewati — file JSON kosong (0 records).")
            print("Jalankan: python scripts\\fetch_obs_local.py --test-api")
            sys.exit(1)
        print("Upload ke litbangweb SFTP...")
        up = upload_obs_exports_to_litbangweb(
            files_filter=[Path(result["file"]).name],
        )
        print(f"  uploaded: {up['uploaded']} file(s) → {up.get('remote_path')}")
        for f in up.get("files", []):
            print(f"    {f['local']} ({f['size']:,} bytes)")


if __name__ == "__main__":
    main()
