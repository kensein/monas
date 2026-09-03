# Deploy PSIMKG — MONAS

Pola sama portal [websitepsimkg](https://github.com/kensein/websitepsimkg): sub-app di Apache vhost portal, PM2, bind localhost only.

## Ringkasan

| Item | Nilai |
|------|-------|
| Deploy path | `/var/www/monas` |
| URL publik | https://psimkg.bmkg.go.id/monas/ |
| Frontend | 127.0.0.1:**3013** (PM2 `monas-web`) |
| Backend/API | 127.0.0.1:**8013** (PM2 `monas-api`) |
| Path prefix | `/monas` |
| Process manager | PM2 (bukan systemd) |

### Port map PSIMKG (hindari bentrok)

| App | FE | API |
|-----|----|----|
| Portal websitepsimkg | — | 3001 |
| P3DN | 3002 | — |
| PSIIDN | 3010 | 8010 |
| Instrument | 3011 | 8011 |
| Otomatisasi | 3012 | 8012 |
| **MONAS** | **3013** | **8013** |

---

## Setup awal di server

```bash
sudo mkdir -p /var/www/monas
sudo chown $USER:$USER /var/www/monas

git clone https://github.com/kensein/monas.git /var/www/monas
cd /var/www/monas
git pull

cp .env.example .env
# Edit: SERVE_READONLY=true, SEED_DEMO_DATA=false, BASE_PATH=/monas
# Peta: CARTO_API_KEY=...  (lalu pm2 restart monas-api)

chmod +x deploy_monas.sh scripts/*.sh
./deploy_monas.sh
```

---

## Deploy ulang

```bash
cd /var/www/monas
git pull
./deploy_monas.sh
```

Atau manual:
```bash
.venv/bin/pip install -r requirements.txt
pm2 startOrReload ecosystem.config.cjs
pm2 save
```

---

## Apache (vhost portal PSIMKG)

Tambahkan **sebelum** `ProxyPass / http://127.0.0.1:3001/`:

```apache
# deploy/apache-monas.conf
ProxyPass        /monas/api http://127.0.0.1:8013/api
ProxyPassReverse /monas/api http://127.0.0.1:8013/api
ProxyPass        /monas     http://127.0.0.1:3013/monas
ProxyPassReverse /monas     http://127.0.0.1:3013/monas
```

```bash
sudo apache2ctl configtest && sudo systemctl reload apache2
```

> Jangan overwrite seluruh vhost live — hanya tambah blok di atas.

---

## PM2

File: `ecosystem.config.cjs`

```bash
pm2 status
pm2 logs monas-api
pm2 logs monas-web
```

---

## Data & NC

- **Alur produksi:** PC fetch obs → litbangweb Docker compute → webpsi serve  
  → `docs/DEPLOY_LITBANGWEB_WEBPSI.md`
- webpsi: `SERVE_READONLY=true` (PM2 ecosystem sudah set)
- Data SQLite/cache: `/var/www/monas/data/`
- Import artifact:
  ```bash
  scripts/sync_artifacts_to_webpsi.sh
  ```

---

## Stack

**Python FastAPI + static HTML/JS** — tidak ada build step. **Tidak diganti React** — latency diatasi dengan precompute + artifact ringan, bukan rewrite frontend.

Frontend production: `server-static.js` (Node)  
Backend production: `uvicorn backend.main:app` via Python venv  
Compute litbangweb: Docker image `monas-compute` (offline)

Dev lokal: `start.bat` / `start.sh` → http://localhost:3013 (BASE_PATH kosong)
