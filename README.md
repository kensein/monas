# NWP Verification Dashboard

Dashboard **display-only** — user tidak perlu upload file model.
Model NC di-sync otomatis dari server **litbangweb** (`/opt/lampp/htdocs/wrf/wrfout/`).

## Arsitektur

```
litbangweb server
├── /opt/lampp/htdocs/wrf/wrfout/*.nc     ← model auto-update (InaNWP asim, dll)
├── /opt/lampp/htdocs/monas/nwp-verify/   ← dashboard (deploy di sini)
└── pipeline (background, tiap 1 jam):
    scan NC baru → interpolasi ke stasiun → HARP verify → simpan skor → tampil di UI
```

User hanya melihat hasil verifikasi HARP: **D+0 (analysis)** sampai **D+7** (168 jam).

## Deploy di litbangweb (sama seperti psiidn)

```bash
chmod +x deploy_litbangweb.sh
./deploy_litbangweb.sh
```

Lalu di server:
```bash
cd /opt/lampp/htdocs/monas/nwp-verify
# Edit .env — isi BMKG_PASSWORD
./start.sh
```

Dashboard: http://202.90.199.54:3013 (atau port yang dibuka)

## Port

| Service | Port |
|---------|------|
| Frontend | **3013** |
| API | **8013** |

## Kenapa Cloud Agent tidak bisa akses C:\ lokal?

| Agent | Berjalan di | Akses file lokal |
|-------|-------------|------------------|
| **Cloud Agent** (ini) | VM Linux remote Cursor | ❌ Tidak bisa `C:\Users\...` |
| **Local Agent / psiidn** | Komputer Anda langsung | ✅ Bisa akses semua file lokal |

File NC 11GB Anda **sudah ada di litbangweb**:
```
/opt/lampp/htdocs/wrf/wrfout/2026070112-d01-asim.nc  (12 GB)
```

Dashboard deploy di litbangweb membaca path itu **langsung** — sama seperti `psiidn_export` yang baca `--input /opt/lampp/htdocs/wrf/wrfout`.

## Fetch Observasi (BMKG API POST)

Token auto-refresh ~47 jam. Pipeline backend fetch observasi otomatis saat di jaringan BMKG.

```
POST /api/v21/user/session/login
POST /api/v21/export/observation/by-station/query  (parameter_names: ["*"])
```

## API

- `GET /api/pipeline/status` — status auto-sync
- `GET /api/pipeline/inventory` — daftar NC di server
- `GET /api/cycles` — init cycles tersedia (D-0)
- `GET /api/verification/scores?init_time=...&lead_time=...`
- `GET /api/verification/ranking`
