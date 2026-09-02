# Flow harian PC → webpsi (sementara sebelum litbangweb online penuh)

Karena akses internet di litbangweb terbatas, **hitung di PC/HPC**, **tampilkan di webpsi**.

## Apakah perlu ganti ke React?

**Tidak.** Stack FastAPI + HTML/Canvas tetap dipakai.

- Latency dashboard datang dari **data** (skor precomputed vs hitung on-request), bukan dari React vs vanilla JS.
- Portal PSIMKG memang React, tapi MONAS adalah **display-only** skor HARP — Canvas 2D sudah cukup dan tanpa build step.
- Ganti React menambah kompleksitas deploy tanpa mempercepat interaksi user jika artifact sudah ringan.

## Arsitektur target

```
PC BMKG / HPC (jam 03:00)
  1. Pull obs BMKG + scan NC lokal (4 model)
  2. Verifikasi HARP paralel (PARALLEL_WORKERS=4)
  3. Export artifact ringan → data/artifacts/latest/
       dashboard.sqlite  (scores, map, ranking, station_series)
       manifest.json
  4. SCP/rsync → webpsi:/var/www/monas/data/artifacts/

webpsi (Apache + PM2 :3013/:8013)
  SERVE_READONLY=true
  Import artifact → SQLite
  UI hanya baca cache (latency rendah, tanpa NC)
```

## Setup PC (sekali)

1. `.env` PC — contoh:
   ```
   USE_LOCAL_OBS_JSON=true
   OBS_EXPORT_DIR=D:\nwp-data\obs
   INANWP_NC_PATH=C:\nwp-data\models\InaNWP
   ...
   PARALLEL_VERIFY=true
   PARALLEL_BACKEND=thread
   ENABLE_PIPELINE_SCHEDULER=false
   SERVE_READONLY=false
   WEBPSI_HOST=psimkg.bmkg.go.id   # atau IP internal
   WEBPSI_USER=your_ssh_user
   ```

2. Windows Task Scheduler → **03:00** setiap hari:
   - Program: `C:\...\monas\scripts\daily_verify_pc.bat`
   - Start in: folder repo `monas`

3. Manual uji:
   ```bat
   scripts\daily_verify_pc.bat --skip-sync
   python scripts\export_light_artifacts.py
   ```

## Setup webpsi (Apache + PM2)

Sudah digambarkan di `DEPLOY_MONAS.md`. Tambahan:

```bash
# di /var/www/monas/.env
SERVE_READONLY=true
ENABLE_PIPELINE_SCHEDULER=false
FORCE_PIPELINE=false
AUTO_SYNC_OBS=false
SEED_DEMO_DATA=false

pm2 startOrReload ecosystem.config.cjs
pm2 save
```

Import artifact setelah sync dari PC:

```bash
./scripts/sync_artifacts_to_webpsi.sh /var/www/monas/data/artifacts/latest
# atau
.venv/bin/python scripts/import_artifacts.py --from data/artifacts/latest
```

## Parallel 4 model (HPC)

| Env | Nilai | Catatan |
|-----|-------|---------|
| `PARALLEL_VERIFY` | `true` | default |
| `PARALLEL_WORKERS` | `4` | 1 worker per model |
| `PARALLEL_BACKEND` | `process` | Linux HPC (CPU-bound NC) |
| `PARALLEL_BACKEND` | `thread` | Windows PC (aman SQLite) |

Tidak menunggu model 1 selesai dulu — pending runs dijalankan bersamaan.

## Isi artifact (ringan)

| Tabel | Dipakai UI |
|-------|------------|
| `verification_scores` | Overview KPI, Scores vs lead |
| `verification_station_scores` | Peta stasiun |
| `ranking_cache` | Ranking cards |
| `station_series_cache` | Detail Stasiun (tanpa forecasts penuh) |
| `model_runs` / `stations` | Cycles & meta |

**Tidak** ikut sync: file NC (GB), tabel `forecasts` penuh, raw obs JSON.

## Checklist harian

- [ ] Task Scheduler 03:00 jalan (`logs/daily_verify.log`)
- [ ] `data/artifacts/latest/manifest.json` terbarui
- [ ] Artifact sampai webpsi + import sukses
- [ ] https://psimkg.bmkg.go.id/monas/ → `/api/health` mode `readonly`
