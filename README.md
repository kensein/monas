# NWP Verification Dashboard

Dashboard interaktif verifikasi model NWP (**InaNWP**, **InaCAWO**, **GFS**, **IFS**) terhadap observasi sinoptik stasiun BMKG, mengikuti metodologi **HARP** point verification.

## Port

| Service | Port |
|---------|------|
| Frontend (UI) | **3013** |
| Backend API | **8013** |

## Menjalankan

```bash
pip install -r requirements.txt
chmod +x start.sh
./start.sh
```

Buka: http://localhost:3013

## Upload file NC

File NC dari komputer lokal (contoh `2026070112-d01-asim.nc`) dapat di-upload via sidebar dashboard **Upload NC**, atau:

```bash
curl -F "file=@/path/to/2026070112-d01-asim.nc" \
  "http://localhost:8013/api/models/upload-nc?model=InaNWP"
```

## Observasi Sinoptik (BMKG API v21)

Set environment variables untuk fetch observasi langsung:

```bash
export BMKG_USERNAME=your_user
export BMKG_PASSWORD=your_password
```

Fetch via API:
```bash
curl -X POST "http://localhost:8013/api/obs/fetch-bmkg?date_from=2025-06-01T00:00:00Z&date_to=2025-06-03T23:59:00Z"
```

Semua parameter Sinoptik dari API Export Sinoptik didukung (lihat `backend/config.py`).

## SFTP litbangweb

Observasi disinkronkan dari `/opt/lampp/htdocs/monas` via SFTP (tombol **Sync SFTP** di dashboard).

## Fitur Dashboard

- **Ranking model** — peringkat skill berdasarkan mean RMSE (semua parameter)
- **Scores vs Lead Time** — klik titik grafik untuk angka detail (RMSE, Bias, MAE, N)
- **Peta stasiun** — klik stasiun untuk tabel fcst vs obs 4 model
- **Detail stasiun** — time series interaktif obs + 4 model

## API Endpoints

- `GET /api/verification/ranking` — ranking model
- `GET /api/verification/scores?parameter=...&models=...`
- `GET /api/verification/map?model=...&parameter=...&lead_time=...`
- `GET /api/station/{id}/detail`
- `POST /api/models/upload-nc`
- `POST /api/obs/sync-sftp`
- `POST /api/demo/seed`
