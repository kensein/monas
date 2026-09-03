#!/usr/bin/env bash
# Harian di litbangweb: crop NC (CDO/ncks) → HARP compute v2 → f32 store → rsync webpsi.
# Tanpa SQLite: output = manifest.json + runs/<model>/<init>/*.f32 + obs/<bulan>/*.f32
# Live log:
#   tail -f /opt/lampp/htdocs/monas/logs/compute.log
#   docker logs -f $(docker ps -q --filter ancestor=monas-compute:latest | head -1)
set -euo pipefail

IMAGE="${IMAGE:-monas-compute:latest}"
ENV_FILE="${ENV_FILE:-/opt/lampp/htdocs/monas/compute.env}"
DATA_DIR="${DATA_DIR:-/opt/lampp/htdocs/monas/compute-data}"
NC_DIR="${NC_DIR:-/opt/lampp/htdocs/wrf/monas_nc}"
OBS_DIR="${OBS_DIR:-/opt/lampp/htdocs/wrf/monas_obs}"
LOG_DIR="${LOG_DIR:-/opt/lampp/htdocs/monas/logs}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/compute.log}"
WEBPSI_HOST="${WEBPSI_HOST:-}"
WEBPSI_USER="${WEBPSI_USER:-}"
WEBPSI_PATH="${WEBPSI_PATH:-/var/www/monas/data/artifacts}"
WEBPSI_SSH_PORT="${WEBPSI_SSH_PORT:-22}"
# Staging SFTP-accessible (pola psiidn_export). webpsi/PC pull via :3346 — jangan andalkan push ke webpsi:22
EXPORT_DIR="${EXPORT_DIR:-/opt/lampp/htdocs/wrf/monas_export}"
RUN_CROP="${RUN_CROP:-true}"
CROP_SCRIPT="${CROP_SCRIPT:-$(cd "$(dirname "$0")" && pwd)/crop_inanwp_cdo.sh}"
SRC_NC="${SRC_NC:-/opt/lampp/htdocs/wrf/wrfout}"
# Harian: 1 run terbaru. Backfill semua: VERIFY_MAX_RUNS=0
VERIFY_MAX_RUNS="${VERIFY_MAX_RUNS:-1}"
# Skip arsip bulanan lama (sinoptik_202606… ~380MB). Harian cukup ~14 hari.
OBS_IMPORT_RECENT_DAYS="${OBS_IMPORT_RECENT_DAYS:-14}"
# Overlay kode tanpa rebuild: CODE_DIR=/opt/lampp/htdocs/monas/src
CODE_DIR="${CODE_DIR:-}"

mkdir -p "$DATA_DIR" "$LOG_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

exec > >(tee -a "$LOG_FILE") 2>&1

if [ "${RUN_CROP}" = "true" ] || [ "${RUN_CROP}" = "1" ]; then
  if [ -x "$CROP_SCRIPT" ] || [ -f "$CROP_SCRIPT" ]; then
    log "Crop CDO/ncks: $SRC_NC → $NC_DIR"
    SRC_DIR="$SRC_NC" DST_DIR="$NC_DIR" bash "$CROP_SCRIPT"
  else
    log "WARN: crop script tidak ada ($CROP_SCRIPT)"
  fi
fi

n_nc=0
shopt -s nullglob
for f in "$NC_DIR"/*.nc; do
  [ -f "$f" ] && n_nc=$((n_nc + 1))
done
if [ "$n_nc" -eq 0 ]; then
  log "ERROR: tidak ada NC crop di $NC_DIR"
  exit 1
fi
log "NC crop siap: $n_nc file di $NC_DIR"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  log "ERROR: image $IMAGE belum di-load"
  exit 1
fi

ENV_ARGS=()
if [ -f "$ENV_FILE" ]; then
  ENV_ARGS+=(--env-file "$ENV_FILE")
fi

CODE_MOUNTS=()
if [ -n "$CODE_DIR" ]; then
  if [ -d "$CODE_DIR/backend" ] && [ -d "$CODE_DIR/scripts" ]; then
    CODE_MOUNTS+=(-v "$CODE_DIR/backend:/app/backend:ro")
    CODE_MOUNTS+=(-v "$CODE_DIR/scripts:/app/scripts:ro")
    log "CODE overlay: $CODE_DIR → /app/{backend,scripts}"
  elif [ -d "$CODE_DIR/backend" ]; then
    CODE_MOUNTS+=(-v "$CODE_DIR/backend:/app/backend:ro")
    log "CODE overlay: $CODE_DIR/backend → /app/backend (scripts dari image)"
  else
    log "WARN: CODE_DIR=$CODE_DIR tidak punya backend/ — diabaikan"
  fi
fi

# Preflight: overlay menimpa /app/backend sepenuhnya — harus berisi modul f32.
# Image lama tanpa rebuild juga butuh overlay lengkap.
need_files=()
if [ ${#CODE_MOUNTS[@]} -gt 0 ]; then
  need_files+=(
    "$CODE_DIR/backend/services/harp_compute.py"
    "$CODE_DIR/backend/services/harp_store.py"
    "$CODE_DIR/scripts/harp_compute.py"
  )
fi
missing=()
for f in "${need_files[@]}"; do
  [ -f "$f" ] || missing+=("$f")
done
if [ ${#missing[@]} -gt 0 ]; then
  log "ERROR: CODE_DIR overlay tidak lengkap (modul f32 hilang). Overlay menimpa /app/backend di container."
  for f in "${missing[@]}"; do
    log "  MISSING: $f"
  done
  log "Perbaiki di PC: git pull origin main, lalu scp -r backend scripts → litbangweb /tmp/,"
  log "  lalu: sudo rm -rf $CODE_DIR/{backend,scripts} && sudo cp -a /tmp/backend /tmp/scripts $CODE_DIR/"
  log "  (atau rebuild image monas-compute:latest yang sudah include PR #23)"
  exit 2
fi

STORE_DIR="$DATA_DIR/artifacts"
# f32 store (PSIIDN-style). KEEP_RUNS_PER_MODEL=0 = simpan semua run.
KEEP_RUNS_PER_MODEL="${KEEP_RUNS_PER_MODEL:-0}"
HARP_EXTRA_ARGS="${HARP_EXTRA_ARGS:-}"   # mis. "--force" atau "--force-obs"

log "HARP compute v2 ($IMAGE) → $STORE_DIR · VERIFY_MAX_RUNS=$VERIFY_MAX_RUNS KEEP_RUNS_PER_MODEL=$KEEP_RUNS_PER_MODEL"
log "Monitor: tail -f $LOG_FILE   |   docker logs -f \$(docker ps -q --filter ancestor=$IMAGE | head -1)"

docker run --rm -i \
  --entrypoint /bin/bash \
  --security-opt seccomp=unconfined \
  "${ENV_ARGS[@]}" \
  "${CODE_MOUNTS[@]}" \
  -v "$NC_DIR:/data/nc:ro" \
  -v "$OBS_DIR:/data/obs:ro" \
  -v "$DATA_DIR:/app/data" \
  -e ARTIFACTS_DIR=/app/data/artifacts \
  -e HARP_STORE_DIR=/app/data/artifacts \
  -e STORE_BACKEND=f32 \
  -e PYTHONUNBUFFERED=1 \
  -e PYTHONIOENCODING=utf-8 \
  -e OPENBLAS_NUM_THREADS=1 \
  -e OMP_NUM_THREADS=1 \
  -e MKL_NUM_THREADS=1 \
  -e NUMEXPR_NUM_THREADS=1 \
  -e VERIFY_MAX_RUNS="$VERIFY_MAX_RUNS" \
  -e KEEP_RUNS_PER_MODEL="$KEEP_RUNS_PER_MODEL" \
  -e USE_DUMMY_MODELS=false \
  -e DISABLE_SFTP=true \
  -e OFFLINE_OBS_MODE=true \
  -e INANWP_NC_PATH=/data/nc \
  -e INACAWO_NC_PATH=/data/nc \
  -e GFS_NC_PATH=/data/nc \
  -e IFS_NC_PATH=/data/nc \
  -e LITBANGWEB_OBS_DIR=/data/obs \
  -e PYTHONPATH=/app \
  "$IMAGE" \
  -c "cd /app; if [ ! -f scripts/harp_compute.py ]; then echo 'ERROR: scripts/harp_compute.py tidak ada — rebuild image atau set CODE_DIR overlay lengkap (backend/services/harp_compute.py + harp_store.py)'; exit 2; fi; if [ ! -f backend/services/harp_compute.py ]; then echo 'ERROR: backend/services/harp_compute.py hilang (overlay tidak lengkap / image lama)'; ls -la backend/services/ 2>/dev/null | head -40; exit 2; fi; exec python -u scripts/harp_compute.py --obs-dir /data/obs --max-runs '$VERIFY_MAX_RUNS' --keep-runs '$KEEP_RUNS_PER_MODEL' $HARP_EXTRA_ARGS"

if [ ! -f "$STORE_DIR/manifest.json" ]; then
  log "ERROR: manifest belum ada di $STORE_DIR"
  exit 1
fi
log "Store OK: $STORE_DIR ($(du -sh "$STORE_DIR" 2>/dev/null | cut -f1))"

# Staging untuk pull SFTP (pola PSIIDN). Path di bawah /opt/lampp/htdocs/wrf — user litbangweb biasanya bisa baca.
if [ -n "$EXPORT_DIR" ]; then
  mkdir -p "$EXPORT_DIR"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
      "$STORE_DIR/manifest.json" "$STORE_DIR/runs" "$STORE_DIR/obs" \
      "$EXPORT_DIR/"
  else
    rm -rf "$EXPORT_DIR/runs" "$EXPORT_DIR/obs"
    cp -a "$STORE_DIR/manifest.json" "$EXPORT_DIR/"
    cp -a "$STORE_DIR/runs" "$STORE_DIR/obs" "$EXPORT_DIR/"
  fi
  log "Export staging: $EXPORT_DIR ($(du -sh "$EXPORT_DIR" 2>/dev/null | cut -f1)) — webpsi/PC pull via SFTP :3346"
  log "  webpsi: ./scripts/pull_artifacts_from_litbangweb.sh"
  log "  PC hub: scripts\\pull_artifacts_via_pc.bat"
fi

# Push langsung litbangweb→webpsi sering gagal (timeout :22). Hanya coba jika WEBPSI_* diisi.
if [ -n "$WEBPSI_HOST" ] && [ -n "$WEBPSI_USER" ]; then
  log "Coba push → ${WEBPSI_USER}@${WEBPSI_HOST}:${WEBPSI_PATH} (bila timeout, pakai pull SFTP dari webpsi/PC)"
  if ssh -o ConnectTimeout=8 -p "$WEBPSI_SSH_PORT" "${WEBPSI_USER}@${WEBPSI_HOST}" "mkdir -p ${WEBPSI_PATH}" 2>/dev/null; then
    if command -v rsync >/dev/null 2>&1; then
      rsync -az --delete -e "ssh -p ${WEBPSI_SSH_PORT}" \
        "$STORE_DIR/manifest.json" "$STORE_DIR/runs" "$STORE_DIR/obs" \
        "${WEBPSI_USER}@${WEBPSI_HOST}:${WEBPSI_PATH}/"
    else
      scp -P "$WEBPSI_SSH_PORT" -r "$STORE_DIR/manifest.json" "$STORE_DIR/runs" "$STORE_DIR/obs" \
        "${WEBPSI_USER}@${WEBPSI_HOST}:${WEBPSI_PATH}/"
    fi
    log "webpsi push selesai (API baca manifest baru otomatis)"
  else
    log "WARN: push webpsi gagal/timeout — biarkan WEBPSI_HOST kosong; pakai pull: scripts/pull_artifacts_from_litbangweb.sh"
  fi
else
  log "WEBPSI_HOST kosong (disarankan). Sync = pull dari webpsi/PC → $EXPORT_DIR"
fi

log "Selesai"
