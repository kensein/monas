#!/bin/bash
# Deploy MONAS compute ke litbangweb (Docker, offline-friendly).
# Host Ubuntu 16.04: JANGAN pip install di host — pakai image monas-compute.
#
# Prasyarat di PC online:
#   scripts/build_compute_image.bat  →  dist/monas-compute.tar
#   scp -P 3346 dist/monas-compute.tar litbangweb@HOST:/home/litbangweb/
#
# Lalu di litbangweb:
#   bash scripts/install_litbangweb_compute.sh /home/litbangweb/monas-compute.tar
#
# Detail: docs/DEPLOY_LITBANGWEB_WEBPSI.md

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "Lihat panduan lengkap: $SCRIPT_DIR/docs/DEPLOY_LITBANGWEB_WEBPSI.md"
echo
echo "Langkah singkat:"
echo "  1) PC:  scripts/build_compute_image.bat"
echo "  2) PC:  scp -P 3346 dist/monas-compute.tar litbangweb@202.90.199.54:/home/litbangweb/"
echo "  3) litbangweb: docker load -i /home/litbangweb/monas-compute.tar"
echo "  4) litbangweb: bash scripts/install_litbangweb_compute.sh"
echo "  5) litbangweb: /opt/lampp/htdocs/monas/scripts/litbangweb_daily_compute.sh"
echo "  6) webpsi: SERVE_READONLY + deploy_monas.sh"
