#!/bin/bash
# Entrypoint MONAS compute container (litbangweb).
set -euo pipefail
cd /app

CMD_NAME="${1:-verify}"
shift || true

export PYTHONPATH=/app
export DISABLE_SFTP="${DISABLE_SFTP:-true}"
export ENABLE_PIPELINE_SCHEDULER="${ENABLE_PIPELINE_SCHEDULER:-false}"
export SEED_DEMO_DATA="${SEED_DEMO_DATA:-false}"
export SERVE_READONLY="${SERVE_READONLY:-false}"
export OFFLINE_OBS_MODE="${OFFLINE_OBS_MODE:-true}"
export USE_LOCAL_OBS_JSON="${USE_LOCAL_OBS_JSON:-true}"
export USE_DUMMY_MODELS="${USE_DUMMY_MODELS:-false}"
# Serial default → live progress log di Docker
export PARALLEL_VERIFY="${PARALLEL_VERIFY:-false}"
export PARALLEL_BACKEND="${PARALLEL_BACKEND:-process}"
export PARALLEL_WORKERS="${PARALLEL_WORKERS:-4}"
# Docker + host lama: OpenBLAS default 16 thread → pthread_create Operation not permitted
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"
export INANWP_NC_PATH="${INANWP_NC_PATH:-/data/nc}"
export INACAWO_NC_PATH="${INACAWO_NC_PATH:-/data/nc}"
export GFS_NC_PATH="${GFS_NC_PATH:-/data/nc}"
export IFS_NC_PATH="${IFS_NC_PATH:-/data/nc}"
export OBS_EXPORT_DIR="${OBS_EXPORT_DIR:-/data/obs}"
export LITBANGWEB_OBS_DIR="${LITBANGWEB_OBS_DIR:-/data/obs}"
export ARTIFACTS_DIR="${ARTIFACTS_DIR:-/app/data/artifacts}"
export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"
export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"
# Batasi berapa run pending diproses (kosong/0 = semua). Harian cukup 1–2 terbaru.
export VERIFY_MAX_RUNS="${VERIFY_MAX_RUNS:-}"
export OBS_IMPORT_RECENT_DAYS="${OBS_IMPORT_RECENT_DAYS:-14}"

mkdir -p /app/data /app/logs "$ARTIFACTS_DIR"

export STORE_BACKEND="${STORE_BACKEND:-f32}"
export HARP_STORE_DIR="${HARP_STORE_DIR:-$ARTIFACTS_DIR}"
export KEEP_RUNS_PER_MODEL="${KEEP_RUNS_PER_MODEL:-0}"

case "$CMD_NAME" in
  verify)
    echo "[monas-compute] HARP compute v2 → f32 store ($HARP_STORE_DIR) · max_runs=${VERIFY_MAX_RUNS:-1}"
    exec python -u scripts/harp_compute.py --obs-dir "$LITBANGWEB_OBS_DIR" --max-runs "${VERIFY_MAX_RUNS:-1}" "$@"
    ;;
  verify-legacy)
    echo "[monas-compute] legacy SQLite verify + export"
    exec python scripts/daily_verify_pc.py --skip-obs --skip-sync "$@"
    ;;
  export)
    echo "[monas-compute] export light artifacts only"
    exec python scripts/export_light_artifacts.py "$@"
    ;;
  import-obs)
    echo "[monas-compute] import obs JSON → SQLite"
    exec python -c "
from backend.services.obs_sync import import_obs_from_json_dir
import os
r = import_obs_from_json_dir(os.environ.get('LITBANGWEB_OBS_DIR','/data/obs'))
print(r)
"
    ;;
  shell)
    exec bash "$@"
    ;;
  *)
    exec "$CMD_NAME" "$@"
    ;;
esac
