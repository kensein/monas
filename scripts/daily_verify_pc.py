#!/usr/bin/env python3
"""Daily PC orchestrator: pull data → verify (parallel) → export light artifacts → sync webpsi.

Jadwalkan via Windows Task Scheduler jam 03:00:
  scripts\\daily_verify_pc.bat

Contoh:
  python scripts/daily_verify_pc.py
  python scripts/daily_verify_pc.py --skip-obs --skip-sync
  python scripts/daily_verify_pc.py --obs-days 3 --workers 4
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("PYTHONPATH", str(ROOT))


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def step_pull_obs(days: int) -> dict:
    log(f"1/4 Fetch observasi BMKG ({days} hari) + import DB...")
    from backend.services.obs_fetcher import sync_observations_recent
    # Prefer local JSON path if configured
    from backend.config import USE_LOCAL_OBS_JSON, OBS_EXPORT_DIR
    if USE_LOCAL_OBS_JSON:
        from backend.services.obs_sync import import_obs_from_json_dir
        result = import_obs_from_json_dir(OBS_EXPORT_DIR)
        # Also refresh recent via API if credentials exist
        try:
            api = sync_observations_recent(days=days)
            result["api_sync"] = api
        except Exception as e:
            result["api_sync_error"] = str(e)
        return result
    return sync_observations_recent(days=days)


def step_pull_nc() -> dict:
    log("2/4 Scan / sync NC lokal (InaNWP, InaCAWO, GFS, IFS)...")
    from backend.services.pipeline import discover_model_runs, register_runs
    runs = discover_model_runs()
    n = register_runs(runs)
    by_model: dict[str, int] = {}
    for r in runs:
        by_model[r["model"]] = by_model.get(r["model"], 0) + 1
    log(f"   Ditemukan {len(runs)} NC · terdaftar {n} · per model: {by_model}")
    return {"discovered": len(runs), "registered": n, "by_model": by_model}


def step_verify(workers: int | None) -> dict:
    log("3/4 Verifikasi HARP (paralel per model)...")
    if workers:
        os.environ["PARALLEL_WORKERS"] = str(workers)
        os.environ["PARALLEL_VERIFY"] = "true"
    from backend.services.pipeline import init_pipeline_db, run_full_pipeline
    init_pipeline_db()
    result = run_full_pipeline(parallel=True)
    log(f"   processed={result.get('processed')} errors={len(result.get('errors') or [])} parallel={result.get('parallel')}")
    return result


def step_export() -> dict:
    log("4/4 Export artifact ringan + rebuild station series cache...")
    from backend.services.artifacts import export_light_artifacts
    manifest = export_light_artifacts()
    log(f"   tag={manifest['tag']} tables={manifest['tables']}")
    return manifest


def step_sync_webpsi() -> dict:
    from backend.config import WEBPSI_HOST, WEBPSI_USER, WEBPSI_PATH, WEBPSI_SSH_PORT, ARTIFACTS_DIR
    if not WEBPSI_HOST or not WEBPSI_USER:
        log("Sync webpsi dilewati (set WEBPSI_HOST + WEBPSI_USER di .env)")
        return {"skipped": True, "reason": "WEBPSI_HOST/USER kosong"}

    src = ARTIFACTS_DIR / "latest"
    if not src.is_dir():
        return {"skipped": True, "reason": "artifacts/latest tidak ada"}

    log(f"Sync → {WEBPSI_USER}@{WEBPSI_HOST}:{WEBPSI_PATH}")
    # Prefer scp/rsync; Windows may use scp from OpenSSH
    remote = f"{WEBPSI_USER}@{WEBPSI_HOST}:{WEBPSI_PATH}/"
    try:
        subprocess.run(
            ["ssh", "-p", str(WEBPSI_SSH_PORT), f"{WEBPSI_USER}@{WEBPSI_HOST}",
             f"mkdir -p {WEBPSI_PATH}"],
            check=False, capture_output=True, text=True,
        )
        # rsync if available
        if shutil_which("rsync"):
            cmd = [
                "rsync", "-avz", "--delete",
                "-e", f"ssh -p {WEBPSI_SSH_PORT}",
                f"{src}/", remote,
            ]
        else:
            # scp -r
            cmd = ["scp", "-P", str(WEBPSI_SSH_PORT), "-r", str(src), f"{WEBPSI_USER}@{WEBPSI_HOST}:{WEBPSI_PATH}/"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            log(f"Sync gagal: {proc.stderr or proc.stdout}")
            return {"ok": False, "stderr": proc.stderr, "stdout": proc.stdout}
        # Trigger import on remote
        import_cmd = (
            f"cd /var/www/monas && .venv/bin/python scripts/import_artifacts.py "
            f"--from {WEBPSI_PATH}/latest"
        )
        subprocess.run(
            ["ssh", "-p", str(WEBPSI_SSH_PORT), f"{WEBPSI_USER}@{WEBPSI_HOST}", import_cmd],
            check=False, capture_output=True, text=True,
        )
        log("Sync + import remote selesai")
        return {"ok": True, "remote": remote}
    except FileNotFoundError as e:
        return {"ok": False, "error": f"ssh/scp tidak tersedia: {e}"}


def shutil_which(cmd: str) -> bool:
    from shutil import which
    return which(cmd) is not None


def main() -> int:
    ap = argparse.ArgumentParser(description="MONAS daily PC → webpsi pipeline")
    ap.add_argument("--skip-obs", action="store_true")
    ap.add_argument("--skip-nc", action="store_true")
    ap.add_argument("--skip-verify", action="store_true")
    ap.add_argument("--skip-export", action="store_true")
    ap.add_argument("--skip-sync", action="store_true")
    ap.add_argument("--obs-days", type=int, default=3)
    ap.add_argument("--workers", type=int, default=None)
    args = ap.parse_args()

    log("=== MONAS daily verify PC ===")
    summary: dict = {"started": datetime.utcnow().isoformat() + "Z"}

    try:
        if not args.skip_obs:
            summary["obs"] = step_pull_obs(args.obs_days)
        if not args.skip_nc:
            summary["nc"] = step_pull_nc()
        if not args.skip_verify:
            summary["verify"] = step_verify(args.workers)
        if not args.skip_export:
            summary["export"] = step_export()
        if not args.skip_sync:
            summary["sync"] = step_sync_webpsi()
    except Exception as e:
        log(f"GAGAL: {e}")
        summary["error"] = str(e)
        _write_log(summary)
        return 1

    summary["finished"] = datetime.utcnow().isoformat() + "Z"
    _write_log(summary)
    log("=== Selesai ===")
    return 0


def _write_log(summary: dict) -> None:
    import json
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    path = log_dir / "daily_verify.log"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(summary, default=str) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
