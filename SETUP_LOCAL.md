# Setup Local — Opsi B (Komputer BMKG)

Panduan menjalankan dashboard verifikasi NWP di **komputer lokal Anda** dengan **Cursor Local Agent**, sebelum deploy ke server PSIMKG.

---

## URL aplikasi

| Lingkungan | URL |
|------------|-----|
| **Dev lokal (PC BMKG)** | http://localhost:3013 |
| **Produksi (server PSIMKG)** | https://psimkg.bmkg.go.id/verifikasi-inanwp/ |

> Bukan `/monas` — subpath resmi di portal PSIMKG adalah **`/verifikasi-inanwp`**.

---

## Ringkasan alur

```
litbangweb (tanpa internet)
    └── file NC (*.nc)  ──SFTP/FileZilla──►  PC BMKG (C:\nwp-data\models\)
                                                    │
                                            Dashboard lokal (:3013)
                                                    │
                                    BMKG API Sinoptik (intranet bmkgsatu)
                                                    │
                              git push ──► server PSIMKG (/var/www/verifikasi-inanwp)
```

---

## Langkah 1 — Pull repo dari GitHub (PC lokal)

```cmd
cd C:\Users\husei\Projects
git clone https://github.com/kensein/monas.git
cd monas
git checkout cursor/psimkg-verifikasi-inanwp-deploy-3ba0
git pull
```

Update rutin sebelum coding:
```cmd
cd C:\Users\husei\Projects\monas
git pull
```

---

## Langkah 2 — Install software di PC BMKG

### Python 3.11 atau 3.12
- Saat install, **centang "Add Python to PATH"**
- Verifikasi: `python --version`

### Cursor Desktop
- Mode **Agent** (Local Agent, bukan Cloud Agent)

---

## Langkah 3 — Folder data model NC

```
C:\nwp-data\models\
├── InaNWP\          ← *-asim.nc
│   └── 2026070112-d01-asim.nc
├── InaCAWO\
├── GFS\
└── IFS\
```

Copy dari litbangweb via FileZilla (SFTP `202.90.199.54:3346`, path `/opt/lampp/htdocs/wrf/wrfout/`).

Atau set satu file di `.env`:
```
LOCAL_NC_PATH=C:\Users\husei\Downloads\2026070112-d01-asim.nc
```

---

## Langkah 4 — Konfigurasi `.env`

```cmd
copy .env.example .env
notepad .env
```

Dev lokal — **BASE_PATH kosong** (default di `.env.example`):
```ini
BMKG_USERNAME=psimkg
BMKG_PASSWORD=<password>
LOCAL_NC_PATH=C:\Users\husei\Downloads\2026070112-d01-asim.nc
SEED_DEMO_DATA=false
FORCE_PIPELINE=true
API_PORT=8013
FRONTEND_PORT=3013
```

---

## Langkah 5 — Install & jalankan

```cmd
python -m pip install -r requirements.txt
start.bat
```

- **Dashboard:** http://localhost:3013
- **API:** http://localhost:8013/docs

---

## Langkah 6 — Test pipeline NC 12GB

```cmd
python scripts\test_nc_pipeline.py --inspect
python scripts\test_nc_pipeline.py --full
```

---

## Langkah 7 — Deploy ke server PSIMKG (nanti)

Deploy path: `/var/www/verifikasi-inanwp`  
Publik: `https://psimkg.bmkg.go.id/verifikasi-inanwp/`

Di server (setelah git clone):
```bash
cd /var/www/verifikasi-inanwp
chmod +x deploy_psimkg.sh scripts/*.sh
./deploy_psimkg.sh
```

Admin portal tambahkan snippet Apache **sebelum** catch-all `:3001`:
```
deploy/apache-verifikasi-inanwp.conf
```

PM2 apps: `verifikasi-inanwp-api` (:8013) + `verifikasi-inanwp-web` (:3013)  
Bind **127.0.0.1** saja — tidak expose ke internet langsung.

Detail lengkap: `DEPLOY_PSIMKG.md`

---

## Troubleshooting

### Dashboard kosong
- Cek http://localhost:8013/api/pipeline/inventory
- Trigger: `curl -X POST http://localhost:8013/api/pipeline/run`

### BMKG API gagal
- Pastikan intranet BMKG
- Cek http://localhost:8013/api/bmkg/token-status

### Port bentrok
- Ubah `API_PORT` / `FRONTEND_PORT` di `.env`

---

## Checklist cepat

- [ ] `git pull` branch `cursor/psimkg-verifikasi-inanwp-deploy-3ba0`
- [ ] `.env` diisi (BMKG password + LOCAL_NC_PATH)
- [ ] `pip install -r requirements.txt`
- [ ] `start.bat` → http://localhost:3013
- [ ] Cursor **Local Agent** (bukan Cloud)
