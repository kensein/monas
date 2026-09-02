# Deploy PSIMKG — Verifikasi InaNWP

Pola sama portal [websitepsimkg](https://github.com/kensein/websitepsimkg): sub-app di Apache vhost portal, PM2, bind localhost only.

## Ringkasan

| Item | Nilai |
|------|-------|
| Deploy path | `/var/www/verifikasi-inanwp` |
| URL publik | https://psimkg.bmkg.go.id/verifikasi-inanwp/ |
| Frontend | 127.0.0.1:**3013** (PM2 `verifikasi-inanwp-web`) |
| Backend/API | 127.0.0.1:**8013** (PM2 `verifikasi-inanwp-api`) |
| Path prefix | `/verifikasi-inanwp` |
| Process manager | PM2 (bukan systemd) |

### Port map PSIMKG (hindari bentrok)

| App | FE | API |
|-----|----|----|
| Portal websitepsimkg | — | 3001 |
| P3DN | 3002 | — |
| PSIIDN | 3010 | 8010 |
| Instrument | 3011 | 8011 |
| Otomatisasi | 3012 | 8012 |
| **Verifikasi InaNWP** | **3013** | **8013** |

---

## Setup awal di server

```bash
sudo mkdir -p /var/www/verifikasi-inanwp
sudo chown $USER:$USER /var/www/verifikasi-inanwp

git clone https://github.com/kensein/monas.git /var/www/verifikasi-inanwp
cd /var/www/verifikasi-inanwp
git checkout cursor/psimkg-verifikasi-inanwp-deploy-3ba0

cp .env.example .env
# Edit: BMKG_PASSWORD, SFTP_PASSWORD, SEED_DEMO_DATA=false

chmod +x deploy_psimkg.sh scripts/*.sh
./deploy_psimkg.sh
```

---

## Deploy ulang

```bash
cd /var/www/verifikasi-inanwp
git pull
./deploy_psimkg.sh
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
# deploy/apache-verifikasi-inanwp.conf
ProxyPass        /verifikasi-inanwp/api http://127.0.0.1:8013/api
ProxyPassReverse /verifikasi-inanwp/api http://127.0.0.1:8013/api
ProxyPass        /verifikasi-inanwp     http://127.0.0.1:3013/verifikasi-inanwp
ProxyPassReverse /verifikasi-inanwp     http://127.0.0.1:3013/verifikasi-inanwp
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
pm2 logs verifikasi-inanwp-api
pm2 logs verifikasi-inanwp-web
```

---

## Data & NC

- Data SQLite/cache: `/var/www/verifikasi-inanwp/data/`
- Sync NC dari litbangweb (cron 6 jam):
  ```bash
  scripts/sync_nc_from_litbangweb.sh
  ```
- Fetch observasi (cron 4 jam):
  ```bash
  scripts/fetch_obs_cron.sh
  ```

---

## Stack note

Repo ini memakai **Python FastAPI + static HTML/JS** (bukan Vite/React/Node Express).  
Pola deploy (subpath, PM2, Apache, port 3013/8013) selaras portal PSIMKG.

Frontend production: `server-static.js` (Node, tanpa build)  
Backend production: `uvicorn backend.main:app` via Python venv

Dev lokal: `start.bat` / `start.sh` → http://localhost:3013 (BASE_PATH kosong)
