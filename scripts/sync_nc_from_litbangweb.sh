#!/bin/bash
# Sync NC files dari litbangweb → server PSIMKG
#
# Cron (setiap 6 jam):
#   0 */6 * * * /var/www/monas/scripts/sync_nc_from_litbangweb.sh
#
# Env: baca dari .env di project root

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

SFTP_HOST="${SFTP_HOST:-202.90.199.54}"
SFTP_PORT="${SFTP_PORT:-3346}"
SFTP_USER="${SFTP_USER:-litbangweb}"
SFTP_NC_REMOTE="${SFTP_NC_REMOTE:-/opt/lampp/htdocs/wrf/wrfout}"
NC_DATA_PATH="${NC_DATA_PATH:-${INANWP_NC_PATH:-$PROJECT_DIR/data/nc}}"
LOG_FILE="${PROJECT_DIR}/logs/nc_sync.log"

mkdir -p "$NC_DATA_PATH" "$(dirname "$LOG_FILE")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

log "Sync NC dari ${SFTP_USER}@${SFTP_HOST}:${SFTP_NC_REMOTE} → ${NC_DATA_PATH}"

# rsync via SSH (jika litbangweb mendukung rsync over SSH)
if command -v rsync >/dev/null 2>&1; then
  RSYNC_RSH="ssh -p ${SFTP_PORT} -o StrictHostKeyChecking=accept-new"
  if rsync -avz --progress \
    -e "$RSYNC_RSH" \
    --include='*-asim.nc' --include='*-cawo.nc' --include='*-gfs.nc' --include='*-ifs.nc' \
    --exclude='*' \
    "${SFTP_USER}@${SFTP_HOST}:${SFTP_NC_REMOTE}/" \
    "${NC_DATA_PATH}/" 2>>"$LOG_FILE"; then
    log "Rsync selesai"
    exit 0
  fi
  log "Rsync gagal, fallback ke SFTP Python..."
fi

# Fallback: Python SFTP sync (paramiko)
export PROJECT_DIR="$PROJECT_DIR"
"$PROJECT_DIR/.venv/bin/python" << PY
import os, sys
from pathlib import Path

project = Path(os.environ["PROJECT_DIR"])
sys.path.insert(0, str(project))
os.chdir(project)

import paramiko
from backend.config import SFTP_HOST, SFTP_PORT, SFTP_USER, SFTP_PASSWORD

remote = os.environ.get("SFTP_NC_REMOTE", "/opt/lampp/htdocs/wrf/wrfout")
local = Path(os.environ.get("NC_DATA_PATH", "data/nc"))
local.mkdir(parents=True, exist_ok=True)

patterns = ("-asim.nc", "-cawo.nc", "-gfs.nc", "-ifs.nc")

transport = paramiko.Transport((SFTP_HOST, SFTP_PORT))
transport.connect(username=SFTP_USER, password=SFTP_PASSWORD)
sftp = paramiko.SFTPClient.from_transport(transport)

for attr in sftp.listdir_attr(remote):
    if not any(attr.filename.endswith(p) for p in patterns):
        continue
    dest = local / attr.filename
    if dest.exists() and dest.stat().st_size == attr.st_size:
        print(f"  skip (unchanged): {attr.filename}")
        continue
    print(f"  download: {attr.filename} ({attr.st_size / 1e9:.2f} GB)")
    sftp.get(f"{remote.rstrip('/')}/{attr.filename}", str(dest))

sftp.close()
transport.close()
print("SFTP sync selesai")
PY

log "Sync NC selesai"

# Trigger pipeline jika API jalan
API_PORT="${API_PORT:-8013}"
if curl -sf -X POST "http://127.0.0.1:${API_PORT}/api/pipeline/run" >/dev/null 2>&1; then
  log "Pipeline triggered via API"
fi
