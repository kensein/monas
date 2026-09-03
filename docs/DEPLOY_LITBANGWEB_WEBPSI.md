# Deploy arsitektur: PC obs → litbangweb compute (Docker) → webpsi serve

## Peran mesin

| Mesin | Jaringan | Tugas |
|-------|---------|--------|
| **PC BMKG** | Intranet BMKG (API obs) | Fetch observasi harian → SFTP ke litbangweb `monas_obs` · build Docker image (online) |
| **litbangweb** (`puslitbang`) | **Tanpa internet** · Docker | Verifikasi HARP dari NC lokal + obs JSON → export artifact ringan |
| **webpsi** | Publik/portal | Apache + PM2 · `SERVE_READONLY` · tampilkan dashboard |

```
PC 02:00  fetch obs BMKG → SFTP → /opt/lampp/htdocs/wrf/monas_obs
litbangweb 04:30  docker monas-compute → verify + export → SCP artifact → webpsi
webpsi  import artifact → https://psimkg.bmkg.go.id/monas/
```

Stack UI **tetap** FastAPI + Canvas (bukan React). Latency diatasi precompute, bukan rewrite frontend.

---

## A. PC — fetch obs harian (otomatis)

### A1. `.env` PC (cuplikan)
```env
BMKG_USERNAME=psimkg
BMKG_PASSWORD=...
OBS_EXPORT_DIR=D:\nwp-data\obs
USE_LOCAL_OBS_JSON=true

SFTP_HOST=202.90.199.54
SFTP_PORT=3346
SFTP_USER=litbangweb
SFTP_PASSWORD=...
SFTP_OBS_PATH=/opt/lampp/htdocs/wrf/monas_obs
DISABLE_SFTP=false
```
> Untuk sync obs, `DISABLE_SFTP` harus **false** (atau jangan set true) agar upload jalan. Hitungan verify di PC tidak wajib.

### A2. Task Scheduler — jam **02:00**
| Field | Nilai |
|-------|--------|
| Program | `...\monas\scripts\daily_obs_pc.bat` |
| Start in | `...\monas` |

Manual uji:
```bat
scripts\daily_obs_pc.bat
```
Isi: `fetch_obs_local.py --days 3 --monthly --sync` → JSON di `D:\nwp-data\obs` + SFTP ke litbangweb.

Tidak perlu `--from-june` tiap hari (hemat waktu); full history sudah pernah diisi.

---

## B. PC — build Docker image (sekali / saat dependency berubah)

Di PC **yang ada Docker Desktop + internet**:

```bat
scripts\build_compute_image.bat
```

Hasil: `dist\monas-compute.tar` (atau `.tar.gz`).

Copy ke litbangweb:
```bat
scp -P 3346 dist\monas-compute.tar litbangweb@202.90.199.54:/home/litbangweb/
```

---

## C. litbangweb — install compute (sekali)

SSH ke `puslitbang` (port 3346):

```bash
# Load image
docker load -i /home/litbangweb/monas-compute.tar
# atau: gunzip -c monas-compute.tar.gz | docker load

# Deploy script + env (dari repo atau copy manual)
sudo mkdir -p /opt/lampp/htdocs/monas/{scripts,logs,compute-data}
sudo cp scripts/litbangweb_daily_compute.sh /opt/lampp/htdocs/monas/scripts/
sudo cp scripts/install_litbangweb_compute.sh /opt/lampp/htdocs/monas/scripts/
sudo chmod +x /opt/lampp/htdocs/monas/scripts/*.sh

sudo cp docker/compute/env.litbangweb.example /opt/lampp/htdocs/monas/compute.env
# Edit compute.env — tambah jika sync otomatis ke webpsi:
#   WEBPSI_HOST=10.21.224.196
#   WEBPSI_USER=vmhosting
#   WEBPSI_SSH_PORT=22

# Atau pakai installer:
sudo bash scripts/install_litbangweb_compute.sh /home/litbangweb/monas-compute.tar
```

### C1. Uji manual
```bash
export WEBPSI_HOST=10.21.224.196 WEBPSI_USER=vmhosting   # opsional
/opt/lampp/htdocs/monas/scripts/litbangweb_daily_compute.sh
```

Mount di dalam container:
- NC: `/opt/lampp/htdocs/wrf/wrfout` → `/data/nc`
- Obs: `/opt/lampp/htdocs/wrf/monas_obs` → `/data/obs`
- Data: `/opt/lampp/htdocs/monas/compute-data` → `/app/data` (SQLite + artifacts)

### C2. Cron compute — jam **04:30**
```cron
30 4 * * * /opt/lampp/htdocs/monas/scripts/litbangweb_daily_compute.sh >> /opt/lampp/htdocs/monas/logs/compute.log 2>&1
```

Default: `USE_DUMMY_MODELS=false` (hanya InaNWP real), `PARALLEL_WORKERS=4`.

---

## D. webpsi — dashboard (Apache + PM2)

Lihat juga `DEPLOY_MONAS.md`.

```bash
cd /var/www/monas
git pull origin main
# .env:
#   SERVE_READONLY=true
#   ENABLE_PIPELINE_SCHEDULER=false
#   FORCE_PIPELINE=false
#   SEED_DEMO_DATA=false
#   BASE_PATH=/monas
./deploy_monas.sh
# atau: pm2 startOrReload ecosystem.config.cjs
```

Import artifact (otomatis dari script litbangweb, atau manual):
```bash
./scripts/sync_artifacts_to_webpsi.sh /var/www/monas/data/artifacts/latest
```

Cek: `curl -s http://127.0.0.1:8013/api/health` → `"mode":"readonly"`.

---

## Jadwal harian (ringkas)

| Waktu | Mesin | Aksi |
|-------|--------|------|
| 02:00 | PC | `daily_obs_pc.bat` → monas_obs |
| 04:30 | litbangweb | Docker verify + export + SCP artifact |
| ~04:45 | webpsi | import + restart API (dari script) |

---

## Catatan Ubuntu 16.04

Host litbangweb **16.04** — jangan `pip install` langsung di host. Semua dependency ada **di dalam image** `monas-compute` (Python 3.11 / Debian bookworm).

Rebuild image di PC bila `requirements.txt` berubah, lalu `docker load` ulang di litbangweb.

---

## Checklist

- [ ] PC: Task Scheduler 02:00 `daily_obs_pc.bat`
- [ ] PC: pernah `build_compute_image.bat` + copy tar ke litbangweb
- [ ] litbangweb: `docker images | grep monas-compute`
- [ ] litbangweb: cron 04:30 `litbangweb_daily_compute.sh`
- [ ] webpsi: `SERVE_READONLY=true`, PM2 monas-api/web
- [ ] URL: https://psimkg.bmkg.go.id/monas/
