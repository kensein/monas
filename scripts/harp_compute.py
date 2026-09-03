#!/usr/bin/env python3
"""HARP compute v2 (f32 store, tanpa SQLite).

Contoh:
  python scripts/harp_compute.py --obs-dir /data/obs --max-runs 1
  python scripts/harp_compute.py --obs-dir D:/nwp-data/obs --max-runs 0      # backfill semua
  python scripts/harp_compute.py --force --force-obs                           # hitung ulang

Env yang dipakai: INANWP_NC_PATH (folder NC crop), HARP_STORE_DIR / ARTIFACTS_DIR (output).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
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


def main() -> int:
    ap = argparse.ArgumentParser(description="MONAS HARP compute → f32 artifacts")
    ap.add_argument("--obs-dir", default=os.getenv("LITBANGWEB_OBS_DIR") or os.getenv("OBS_EXPORT_DIR") or "/data/obs")
    ap.add_argument("--max-runs", type=int, default=int(os.getenv("VERIFY_MAX_RUNS", "1") or "0"),
                    help="0 = semua run pending")
    ap.add_argument("--models", default="", help="comma list, kosong = semua")
    ap.add_argument("--force", action="store_true", help="hitung ulang run yang sudah ada")
    ap.add_argument("--force-obs", action="store_true", help="parse ulang JSON obs → cube")
    ap.add_argument("--keep-runs", type=int, default=int(os.getenv("KEEP_RUNS_PER_MODEL", "0") or "0"),
                    help="simpan N run terbaru per model (0 = semua)")
    args = ap.parse_args()

    from backend.services.harp_compute import run_pipeline

    result = run_pipeline(
        obs_json_dir=Path(args.obs_dir),
        max_runs=args.max_runs,
        force=args.force,
        force_obs=args.force_obs,
        keep_runs_per_model=args.keep_runs,
        models=[m.strip() for m in args.models.split(",") if m.strip()] or None,
    )
    print(json.dumps({k: v for k, v in result.items() if k != "processed"}, indent=2, default=str))
    return 1 if result.get("errors") and not result.get("processed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
