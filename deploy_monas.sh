#!/bin/bash
# Deploy MONAS ke server PSIMKG (pola portal websitepsimkg)
#
# Jalankan DI SERVER setelah git clone:
#   sudo mkdir -p /var/www/monas
#   sudo chown $USER:$USER /var/www/monas
#   git clone https://github.com/kensein/monas.git /var/www/monas
#   cd /var/www/monas && git pull
#
# Deploy ulang:
#   cd /var/www/monas && ./deploy_monas.sh
#
# Publik: https://psimkg.bmkg.go.id/monas/

set -euo pipefail

DEPLOY_PATH="${DEPLOY_PATH:-/var/www/monas}"
cd "$DEPLOY_PATH"

echo "=== Deploy MONAS → PSIMKG ==="
echo "  Path: $DEPLOY_PATH"
echo "  URL:  https://psimkg.bmkg.go.id/monas/"

mkdir -p data/{nc,obs,cache} logs

if git rev-parse --git-dir >/dev/null 2>&1; then
  echo ""
  echo "[1/4] git pull..."
  git pull --ff-only || echo "  (git pull skipped — lanjut)"
fi

echo ""
echo "[2/4] Python venv + dependencies..."
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo ">>> Buat .env — isi BMKG_PASSWORD dan SFTP_PASSWORD"
fi

grep -q '^SEED_DEMO_DATA=' .env || echo 'SEED_DEMO_DATA=false' >> .env
grep -q '^BASE_PATH=' .env || echo 'BASE_PATH=/monas' >> .env
grep -q '^API_HOST=' .env || echo 'API_HOST=127.0.0.1' >> .env
grep -q '^CORS_ORIGIN=' .env || echo 'CORS_ORIGIN=https://psimkg.bmkg.go.id' >> .env

echo ""
echo "[3/4] PM2 startOrReload..."
if ! command -v pm2 >/dev/null 2>&1; then
  echo "ERROR: PM2 tidak ditemukan. Install: npm install -g pm2"
  exit 1
fi

pm2 startOrReload ecosystem.config.cjs
pm2 save

echo ""
echo "[4/4] Apache — tambahkan snippet ke vhost portal:"
echo "  deploy/apache-monas.conf"
echo ""
echo "  sudo apache2ctl configtest && sudo systemctl reload apache2"
echo ""
echo "=== Selesai ==="
echo "  Lokal test:  curl http://127.0.0.1:8013/api/health"
echo "  Publik:      https://psimkg.bmkg.go.id/monas/"
echo "  PM2 status:  pm2 status"
