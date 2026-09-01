# NWP Verification Dashboard

Dashboard interaktif verifikasi model NWP (**InaNWP**, **InaCAWO**, **GFS**, **IFS**) terhadap observasi sinoptik stasiun BMKG, mengikuti metodologi **HARP** point verification.

## Port

| Service | Port |
|---------|------|
| Frontend (UI) | **3013** |
| Backend API | **8013** |

## Menjalankan

```bash
cp .env.example .env   # edit username/password
pip install -r requirements.txt
chmod +x start.sh
./start.sh
```

Buka: http://localhost:3013

---

## File NC 11GB — Gunakan Path Lokal

Browser **tidak bisa** upload file 11GB. Jalankan `./start.sh` di **komputer Windows** yang punya file, lalu:

1. Isi path: `C:\Users\husei\Downloads\2026070112-d01-asim.nc`
2. Klik **Muat dari Path Lokal**

Atau set di `.env`:
```
LOCAL_NC_PATH=C:\Users\husei\Downloads\2026070112-d01-asim.nc
```
Lalu klik **Muat LOCAL_NC_PATH (.env)**

Drag & drop hanya untuk file **< 2GB**.

---

## Fetch Observasi Sinoptik (BMKG API)

Sesuai PPTX **API Export Sinoptik (search api)**:

| Step | Method | URL |
|------|--------|-----|
| 1. Login | **POST** | `https://bmkgsatu.bmkg.go.id/api/v21/user/session/login` |
| 2. Export | **POST** | `https://bmkgsatu.bmkg.go.id/api/v21/export/observation/by-station/query` |

Body export (semua parameter):
```json
{
  "data_type": "sinoptik",
  "parameter_names": ["*"],
  "station_wmo_ids": ["*"],
  "date_from": "2025-06-01T00:00:00Z",
  "date_to": "2025-06-03T23:59:00Z",
  "order_timestamp_code": 1
}
```

**Token auto-refresh** setiap ~47 jam (cache: `data/cache/bmkg_token.json`).

### Setup `.env`
```
BMKG_USERNAME=psimkg
BMKG_PASSWORD=your_password
```

### Via Dashboard
Sidebar → Observasi Sinoptik → pilih tanggal → **Fetch Observasi (POST API)**

> API hanya bisa diakses dari **jaringan BMKG** (diblokir Cloudflare dari internet publik).

---

## SFTP litbangweb

Observasi disinkronkan dari `/opt/lampp/htdocs/monas` via SFTP (tombol **Sync SFTP**).

## Fitur Dashboard

- **Ranking model** — mean RMSE semua parameter
- **Drag & drop NC** + path lokal untuk file besar
- **Scores vs Lead Time** — klik titik untuk angka detail
- **Peta stasiun** — klik stasiun untuk fcst vs obs 4 model
- **Token status** — indikator BMKG API + auto-refresh

## API Endpoints

- `GET /api/bmkg/token-status` — status token + auto-login
- `POST /api/bmkg/refresh-token` — force refresh token
- `POST /api/obs/fetch-bmkg` — fetch observasi (POST body JSON)
- `POST /api/models/load-local-path` — baca NC dari path lokal
- `POST /api/models/load-default-local` — baca dari LOCAL_NC_PATH
- `GET /api/jobs/{id}` — progress proses NC background
- `GET /api/verification/ranking`
- `GET /api/verification/scores`
