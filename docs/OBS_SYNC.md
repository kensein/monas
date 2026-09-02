# Sync Observasi — PC Lokal → litbangweb

litbangweb **tidak punya internet** → tidak bisa login BMKG API (token 48 jam).
Solusi: **download di PC BMKG** (intranet) → **SFTP ke litbangweb** (otomatis).

## Alur

```
PC BMKG (intranet)                         litbangweb (offline)
─────────────────                         ────────────────────
BMKG API login (auto token)               /opt/lampp/htdocs/wrf/monas_obs/
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
SFTP_OBS_PATH=/opt/lampp/htdocs/wrf/monas_obs
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

```cmd
REM Diagnostik login + format response API
python scripts\fetch_obs_local.py --test-api

REM Test write permission SFTP ke litbangweb
python scripts\fetch_obs_local.py --test-sftp

REM Coba rentang saat init NC (Juli 2026)
python scripts\fetch_obs_local.py --from 2026-07-01T00:00:00Z --to 2026-07-05T23:59:00Z
```

| Error | Solusi |
|-------|--------|
| `records: 0` | `--test-api` — cek password, intranet, coba tanggal Juli 2026 |
| SFTP `No such file` | `--test-sftp` — path default sekarang `/opt/lampp/htdocs/wrf/monas_obs` |
| Upload file kosong | Normal — upload dilewati jika 0 records |
