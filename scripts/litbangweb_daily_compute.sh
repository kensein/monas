#!/usr/bin/env bash
# Harian di litbangweb: crop (opsional) + verify InaNWP + export artifact.
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
RUN_CROP="${RUN_CROP:-true}"
CROP_SCRIPT="${CROP_SCRIPT:-$(cd "$(dirname "$0")" && pwd)/crop_inanwp_cdo.sh}"
SRC_NC="${SRC_NC:-/opt/lampp/htdocs/wrf/wrfout}"
# Serial = live progress. Paralel: PARALLEL_VERIFY=true
PARALLEL_VERIFY="${PARALLEL_VERIFY:-false}"
# Harian: 1 run terbaru. Backfill semua: VERIFY_MAX_RUNS=0
VERIFY_MAX_RUNS="${VERIFY_MAX_RUNS:-1}"
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

log "Verify+export ($IMAGE) · PARALLEL_VERIFY=$PARALLEL_VERIFY VERIFY_MAX_RUNS=$VERIFY_MAX_RUNS"
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
  -e PYTHONUNBUFFERED=1 \
  -e PYTHONIOENCODING=utf-8 \
  -e OPENBLAS_NUM_THREADS=1 \
  -e OMP_NUM_THREADS=1 \
  -e MKL_NUM_THREADS=1 \
  -e NUMEXPR_NUM_THREADS=1 \
  -e PARALLEL_VERIFY="$PARALLEL_VERIFY" \
  -e PARALLEL_WORKERS="${PARALLEL_WORKERS:-4}" \
  -e VERIFY_MAX_RUNS="$VERIFY_MAX_RUNS" \
  -e USE_DUMMY_MODELS="${USE_DUMMY_MODELS:-false}" \
  "$IMAGE" \
  -c 'sed -i "s/\r$//" /app/entrypoint.sh /app/scripts/*.sh 2>/dev/null; python -u -c "from backend.services.obs_fetcher import init_db; from backend.services.pipeline import init_pipeline_db; init_db(); init_pipeline_db(); print(\"DB init OK\", flush=True)"; exec /bin/bash /app/entrypoint.sh verify'

ARTIFACT_SRC="$DATA_DIR/artifacts/latest"
if [ ! -d "$ARTIFACT_SRC" ]; then
  log "ERROR: artifact belum ada di $ARTIFACT_SRC"
  exit 1
fi
log "Artifact OK: $ARTIFACT_SRC"

if [ -n "$WEBPSI_HOST" ] && [ -n "$WEBPSI_USER" ]; then
  log "Sync artifact → ${WEBPSI_USER}@${WEBPSI_HOST}:${WEBPSI_PATH}"
  ssh -p "$WEBPSI_SSH_PORT" "${WEBPSI_USER}@${WEBPSI_HOST}" "mkdir -p ${WEBPSI_PATH}"
  if command -v rsync >/dev/null 2>&1; then
    rsync -avz --delete -e "ssh -p ${WEBPSI_SSH_PORT}" \
      "${ARTIFACT_SRC}/" "${WEBPSI_USER}@${WEBPSI_HOST}:${WEBPSI_PATH}/latest/"
  else
    scp -P "$WEBPSI_SSH_PORT" -r "${ARTIFACT_SRC}" \
      "${WEBPSI_USER}@${WEBPSI_HOST}:${WEBPSI_PATH}/"
  fi
  ssh -p "$WEBPSI_SSH_PORT" "${WEBPSI_USER}@${WEBPSI_HOST}" \
    "cd /var/www/monas && .venv/bin/python scripts/import_artifacts.py --from ${WEBPSI_PATH}/latest && pm2 restart monas-api"
  log "webpsi import + restart monas-api selesai"
else
  log "WEBPSI_HOST/USER kosong — sync dilewati"
fi

log "Selesai"
