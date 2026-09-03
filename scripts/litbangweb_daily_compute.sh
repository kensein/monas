#!/usr/bin/env bash
# Harian di litbangweb: import obs JSON + verify InaNWP + export artifact.
# Cron contoh (setelah obs dari PC masuk):
#   30 4 * * * /opt/lampp/htdocs/monas/scripts/litbangweb_daily_compute.sh >> /opt/lampp/htdocs/monas/logs/compute.log 2>&1
set -euo pipefail

IMAGE="${IMAGE:-monas-compute:latest}"
ENV_FILE="${ENV_FILE:-/opt/lampp/htdocs/monas/compute.env}"
DATA_DIR="${DATA_DIR:-/opt/lampp/htdocs/monas/compute-data}"
NC_DIR="${NC_DIR:-/opt/lampp/htdocs/wrf/wrfout}"
OBS_DIR="${OBS_DIR:-/opt/lampp/htdocs/wrf/monas_obs}"
WEBPSI_HOST="${WEBPSI_HOST:-}"
WEBPSI_USER="${WEBPSI_USER:-}"
WEBPSI_PATH="${WEBPSI_PATH:-/var/www/monas/data/artifacts}"
WEBPSI_SSH_PORT="${WEBPSI_SSH_PORT:-22}"

mkdir -p "$DATA_DIR" "$(dirname "$DATA_DIR")/logs"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  log "ERROR: image $IMAGE belum di-load. docker load -i monas-compute.tar"
  exit 1
fi

ENV_ARGS=()
if [ -f "$ENV_FILE" ]; then
  ENV_ARGS+=(--env-file "$ENV_FILE")
fi

log "Import obs + verify + export ($IMAGE)"
# --entrypoint /bin/bash: image build di Windows bisa punya entrypoint CRLF
# OPENBLAS_NUM_THREADS=1 + seccomp=unconfined: host Docker lama (Ubuntu 16.04)
docker run --rm \
  --entrypoint /bin/bash \
  --security-opt seccomp=unconfined \
  "${ENV_ARGS[@]}" \
  -v "$NC_DIR:/data/nc:ro" \
  -v "$OBS_DIR:/data/obs:ro" \
  -v "$DATA_DIR:/app/data" \
  -e ARTIFACTS_DIR=/app/data/artifacts \
  -e OPENBLAS_NUM_THREADS=1 \
  -e OMP_NUM_THREADS=1 \
  -e MKL_NUM_THREADS=1 \
  -e NUMEXPR_NUM_THREADS=1 \
  -e PARALLEL_WORKERS="${PARALLEL_WORKERS:-4}" \
  -e USE_DUMMY_MODELS="${USE_DUMMY_MODELS:-false}" \
  "$IMAGE" \
  -c 'sed -i "s/\r$//" /app/entrypoint.sh /app/scripts/*.sh 2>/dev/null; exec /bin/bash /app/entrypoint.sh verify'

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
  log "WEBPSI_HOST/USER kosong — sync dilewati. Set di environment atau compute.env"
fi

log "Selesai"
