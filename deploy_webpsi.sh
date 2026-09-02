#!/bin/bash
# Deploy NWP Verification Dashboard ke server webpsi (pola psiidn)
#
# Prasyarat di webpsi:
#   - Python 3.11+, Node/npm (untuk PM2), Apache2
#   - Akses SFTP ke litbangweb (202.90.199.54:3346)
#   - Akses intranet BMKG API (bmkgsatu.bmkg.go.id)
#
# Usage:
#   chmod +x deploy_webpsi.sh
#   ./deploy_webpsi.sh
#
# Env override:
#   WEBPSI_HOST=webpsi.bmkg.go.id WEBPSI_USER=deploy ./deploy_webpsi.sh

set -euo pipefail

REPO="${REPO:-$(cd "$(dirname "$0")" && pwd)}"
WEBPSI_HOST="${WEBPSI_HOST:-webpsi}"
WEBPSI_USER="${WEBPSI_USER:-$(whoami)}"
WEBPSI_PORT="${WEBPSI_PORT:-22}"
DEPLOY_PATH="${DEPLOY_PATH:-/var/www/nwp-verify}"

SFTP_HOST="${SFTP_HOST:-202.90.199.54}"
SFTP_PORT="${SFTP_PORT:-3346}"
SFTP_USER="${SFTP_USER:-litbangweb}"
SFTP_NC_REMOTE="${SFTP_NC_REMOTE:-/opt/lampp/htdocs/wrf/wrfout}"
NC_DATA_PATH="${NC_DATA_PATH:-$DEPLOY_PATH/data/nc}"

SSH_OPTS="-p ${WEBPSI_PORT} -o StrictHostKeyChecking=accept-new"
RSYNC_SSH="ssh ${SSH_OPTS}"

echo "=== Deploy NWP Verification → webpsi ==="
echo "  Host:   ${WEBPSI_USER}@${WEBPSI_HOST}:${DEPLOY_PATH}"
echo "  NC dir: ${NC_DATA_PATH}"

# 1. Sync aplikasi ke webpsi
echo ""
echo "[1/5] Rsync aplikasi..."
rsync -avz --delete \
  -e "${RSYNC_SSH}" \
  --exclude '.git' \
  --exclude 'data/' \
  --exclude '.env' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.venv' \
  "${REPO}/" "${WEBPSI_USER}@${WEBPSI_HOST}:${DEPLOY_PATH}/"

# 2. Setup remote: venv, .env, dirs
echo ""
echo "[2/5] Setup Python venv & direktori..."
ssh ${SSH_OPTS} "${WEBPSI_USER}@${WEBPSI_HOST}" bash -s << REMOTE
set -euo pipefail
cd "${DEPLOY_PATH}"

mkdir -p data/{nc,obs,cache} logs deploy

if [ ! -f .env ]; then
  cp .env.example .env
  echo ""
  echo ">>> EDIT .env: BMKG_PASSWORD, SFTP_PASSWORD, SEED_DEMO_DATA=false"
fi

python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

# Production .env overrides (append if not present)
grep -q 'SEED_DEMO_DATA' .env || cat >> .env << 'ENV'

# --- webpsi production ---
SEED_DEMO_DATA=false
FORCE_PIPELINE=true
AUTO_SYNC_OBS=true
INANWP_NC_PATH=${NC_DATA_PATH}
INACAWO_NC_PATH=${NC_DATA_PATH}
GFS_NC_PATH=${NC_DATA_PATH}
IFS_NC_PATH=${NC_DATA_PATH}
API_PORT=8013
FRONTEND_PORT=3013
ENV

chmod +x start.sh scripts/*.sh 2>/dev/null || true
REMOTE

# 3. Install PM2 ecosystem
echo ""
echo "[3/5] PM2 setup..."
ssh ${SSH_OPTS} "${WEBPSI_USER}@${WEBPSI_HOST}" bash -s << REMOTE
set -euo pipefail
cd "${DEPLOY_PATH}"

if ! command -v pm2 >/dev/null 2>&1; then
  echo "PM2 tidak ditemukan — install: npm install -g pm2"
  exit 1
fi

pm2 delete nwp-verify-api nwp-verify-frontend 2>/dev/null || true
pm2 start ecosystem.config.js
pm2 save
REMOTE

# 4. Apache config
echo ""
echo "[4/5] Apache reverse proxy..."
ssh ${SSH_OPTS} "${WEBPSI_USER}@${WEBPSI_HOST}" bash -s << REMOTE
set -euo pipefail
APACHE_CONF="/etc/apache2/sites-available/nwp-verify.conf"
sudo cp "${DEPLOY_PATH}/deploy/apache-nwp-verify.conf" "\${APACHE_CONF}"
sudo sed -i "s|/var/www/nwp-verify|${DEPLOY_PATH}|g" "\${APACHE_CONF}"
sudo a2enmod proxy proxy_http headers rewrite 2>/dev/null || true
sudo a2ensite nwp-verify.conf 2>/dev/null || true
sudo apache2ctl configtest && sudo systemctl reload apache2
REMOTE

# 5. Cron: sync NC dari litbangweb (setiap 6 jam)
echo ""
echo "[5/5] Cron sync NC..."
ssh ${SSH_OPTS} "${WEBPSI_USER}@${WEBPSI_HOST}" bash -s << REMOTE
set -euo pipefail
CRON_LINE="0 */6 * * * ${DEPLOY_PATH}/scripts/sync_nc_from_litbangweb.sh >> ${DEPLOY_PATH}/logs/nc_sync.log 2>&1"
(crontab -l 2>/dev/null | grep -v sync_nc_from_litbangweb || true; echo "\${CRON_LINE}") | crontab -
echo "Cron NC sync: setiap 6 jam"
REMOTE

echo ""
echo "=== Deploy selesai ==="
echo "  Dashboard: https://\${WEBPSI_HOST:-webpsi}/nwp-verify/"
echo "  API docs:  https://\${WEBPSI_HOST:-webpsi}/nwp-verify/api/docs"
echo "  PM2:       ssh ${WEBPSI_USER}@${WEBPSI_HOST} 'pm2 status'"
echo ""
echo "Sync NC manual: ssh ${WEBPSI_USER}@${WEBPSI_HOST} '${DEPLOY_PATH}/scripts/sync_nc_from_litbangweb.sh'"
