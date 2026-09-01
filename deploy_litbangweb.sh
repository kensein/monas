#!/bin/bash
# Deploy NWP Verification Dashboard ke server litbangweb
# Jalankan dari komputer yang bisa SSH/SFTP ke litbangweb

set -e
DEPLOY_PATH="${DEPLOY_PATH:-/opt/lampp/htdocs/monas/nwp-verify}"
REPO="${REPO:-$(dirname "$0")}"

echo "=== Deploy ke litbangweb: $DEPLOY_PATH ==="

ssh -p 3346 litbangweb@202.90.199.54 "mkdir -p $DEPLOY_PATH"

rsync -avz -e "ssh -p 3346" \
  --exclude '.git' --exclude 'data/' --exclude '.env' --exclude '__pycache__' \
  "$REPO/" litbangweb@202.90.199.54:"$DEPLOY_PATH/"

ssh -p 3346 litbangweb@202.90.199.54 << REMOTE
cd $DEPLOY_PATH
cp -n .env.example .env 2>/dev/null || true

# Path NC langsung dari server (TIDAK perlu upload)
cat >> .env << 'ENV'

# Auto-read NC dari wrfout (sama seperti psiidn)
INANWP_NC_PATH=/opt/lampp/htdocs/wrf/wrfout
INACAWO_NC_PATH=/opt/lampp/htdocs/wrf/wrfout
GFS_NC_PATH=/opt/lampp/htdocs/wrf/wrfout
IFS_NC_PATH=/opt/lampp/htdocs/wrf/wrfout
FORCE_PIPELINE=true
BMKG_USERNAME=psimkg
ENV

python3 -m venv .venv 2>/dev/null || true
.venv/bin/pip install -q -r requirements.txt

# systemd service (optional)
echo "Jalankan: cd $DEPLOY_PATH && ./start.sh"
REMOTE

echo "=== Selesai. Dashboard akan baca NC langsung dari /opt/lampp/htdocs/wrf/wrfout/ ==="
