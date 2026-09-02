# Sync Observasi — PC Lokal → litbangweb

litbangweb **tidak punya internet** → tidak bisa login BMKG API (token 48 jam).
Solusi: **download di PC BMKG** (intranet) → **SFTP ke litbangweb** (otomatis).

## Alur

```
PC BMKG (intranet)                         litbangweb (offline)
─────────────────                         ────────────────────
BMKG API login (auto token)               /opt/lampp/htdocs/monas/obs/
     │                                    sinoptik_YYYYMMDD_....json
     ▼                                              │
D:\nwp-data\obs\  ──── SFTP :3346 ────────────────►│
     │                                              ▼
     │                                    Pipeline import JSON → SQLite
     │                                    HARP verifikasi vs NC lokal
```

## API (dari PPTX Export Sinoptik v21)

**Login** (token 48 jam):
```
POST https://bmkgsatu.bmkg.go.id/api/v21/user/session/login
{"username": "psimkg", "password": "..."}
```

**Export observasi** (semua parameter, chunk ≤4 hari):
```
POST https://bmkgsatu.bmkg.go.id/api/v21/export/observation/by-station/query
Authorization: Bearer <token>
{
  "data_type": "sinoptik",
  "parameter_names": ["*"],
  "station_wmo_ids": ["*"],
  "date_from": "2026-07-01T00:00:00Z",
  "date_to": "2026-07-05T23:59:00Z",
  "order_timestamp_code": 1
}
```

Implementasi sudah ada di `backend/services/bmkg_auth.py` + `bmkg_export.py`.

---

## Setup `.env` (PC lokal)

```ini
BMKG_USERNAME=psimkg
BMKG_PASSWORD=<password>

# Folder download JSON (drive D)
OBS_EXPORT_DIR=D:\nwp-data\obs

# SFTP litbangweb
SFTP_HOST=202.90.199.54
SFTP_PORT=3346
SFTP_USER=litbangweb
SFTP_PASSWORD=<password litbangweb>
SFTP_OBS_PATH=/opt/lampp/htdocs/monas/obs
```

Buat folder:
```cmd
mkdir D:\nwp-data\obs
```

---

## Jalankan manual

```cmd
cd C:\Users\husei\OneDrive\Documents\Project\monas

REM 1. Download saja (10 hari terakhir)
python scripts\fetch_obs_local.py --days 10

REM 2. Download + upload ke litbangweb
python scripts\fetch_obs_local.py --days 10 --sync

REM 3. Atau double-click
scripts\fetch_and_sync_obs.bat
```

---

## Otomatis (Windows Task Scheduler)

1. Buka **Task Scheduler** → Create Task
2. Trigger: **Daily** jam 06:00 (atau setiap 2 hari — data obs tidak perlu tiap jam)
3. Action: Start program
   - Program: `C:\Users\husei\OneDrive\Documents\Project\monas\scripts\fetch_and_sync_obs.bat`
4. Run whether user is logged on or not (jika PC intranet selalu nyala)

---

## litbangweb — terima & pakai obs

Di `.env` litbangweb:
```ini
OFFLINE_OBS_MODE=true
LITBANGWEB_OBS_DIR=/opt/lampp/htdocs/monas/obs
SEED_DEMO_DATA=false
INANWP_NC_PATH=/opt/lampp/htdocs/wrf/wrfout
```

Pipeline otomatis import JSON dari folder `obs/` sebelum verifikasi HARP.

Test import manual:
```bash
cd /opt/lampp/htdocs/monas/nwp-verify
.venv/bin/python -c "
from backend.services.obs_sync import import_obs_from_json_dir
print(import_obs_from_json_dir('/opt/lampp/htdocs/monas/obs'))
"
```

---

## Troubleshooting

| Error | Solusi |
|-------|--------|
| `can't open file test_nc_pipeline.py` | `git pull` — script belum ada di repo lokal |
| Login BMKG gagal (Cloudflare) | Pastikan PC di **intranet BMKG** |
| SFTP upload gagal | Cek password litbangweb, port 3346 |
| litbangweb obs kosong | Jalankan `--sync` dari PC lokal dulu |
