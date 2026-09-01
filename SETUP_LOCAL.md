# Setup Local — Opsi B (Komputer BMKG)

Panduan menjalankan dashboard verifikasi NWP di **komputer lokal Anda** dengan **Cursor Local Agent**, karena server litbangweb tidak punya akses internet.

---

## Ringkasan alur

```
litbangweb (tanpa internet)
    └── file NC (*.nc)  ──SFTP/FileZilla──►  PC BMKG (C:\nwp-data\models\)
                                                    │
                                            Dashboard lokal (:3013)
                                                    │
                                    BMKG API Sinoptik (intranet bmkgsatu)
```

Anda **tidak upload** file lewat browser. Copy NC ke folder lokal → pipeline otomatis verifikasi → dashboard tampilkan hasil HARP (D+0 s/d D+7).

---

## Langkah 1 — Install software di PC BMKG

### 1.1 Cursor Desktop
- Download & install dari https://cursor.com (via jaringan yang bisa akses, atau installer offline jika ada)
- Login akun Cursor

### 1.2 Python 3.11 atau 3.12
- Download: https://www.python.org/downloads/
- Saat install, **centang "Add Python to PATH"**
- Verifikasi di CMD:
  ```cmd
  python --version
  pip --version
  ```

### 1.3 Git (opsional, untuk clone repo)
- https://git-scm.com/download/win

---

## Langkah 2 — Clone / copy project

### Opsi A: Clone dari GitHub (jika PC bisa akses GitHub)
```cmd
cd C:\Users\husei\Projects
git clone https://github.com/kensein/monas.git
cd monas
git checkout cursor/nwp-verification-dashboard-design-51e8
```

### Opsi B: Copy folder dari USB / shared drive
Copy seluruh folder project ke misalnya:
```
C:\Users\husei\Projects\monas\
```

---

## Langkah 3 — Buat folder data model NC

Buat struktur folder untuk file NC (copy dari litbangweb):

```
C:\nwp-data\models\
├── InaNWP\          ← taruh *-asim.nc di sini
│   └── 2026070112-d01-asim.nc
├── InaCAWO\         ← *-cawo.nc
├── GFS\             ← *-gfs.nc
└── IFS\             ← *-ifs.nc
```

### Copy NC dari litbangweb (SFTP internal — tanpa internet publik)

Pakai **FileZilla** (sama seperti gambar SFTP Anda):

| Field | Value |
|-------|-------|
| Protocol | SFTP |
| Host | `202.90.199.54` |
| Port | `3346` |
| User | `litbangweb` |
| Password | (password litbangweb) |

**Remote path NC:**
```
/opt/lampp/htdocs/wrf/wrfout/
```

**Download ke lokal:**
```
C:\nwp-data\models\InaNWP\
```

File contoh: `2026070112-d01-asim.nc` (~12 GB) — cukup copy sekali, lalu replace saat ada run baru.

---

## Langkah 4 — Konfigurasi `.env`

Di folder project, copy `.env.example` → `.env`:

```cmd
cd C:\Users\husei\Projects\monas
copy .env.example .env
notepad .env
```

Isi `.env`:

```ini
# BMKG Sinoptik API (intranet BMKG)
BMKG_USERNAME=psimkg
BMKG_PASSWORD=psimkg2025!
BMKG_API_BASE=https://bmkgsatu.bmkg.go.id

# Folder NC lokal (Windows)
INANWP_NC_PATH=C:\nwp-data\models\InaNWP
INACAWO_NC_PATH=C:\nwp-data\models\InaCAWO
GFS_NC_PATH=C:\nwp-data\models\GFS
IFS_NC_PATH=C:\nwp-data\models\IFS

# Pipeline scan folder lokal saat startup
FORCE_PIPELINE=true

# Port
API_PORT=8013
FRONTEND_PORT=3013
```

---

## Langkah 5 — Install dependency Python

```cmd
cd C:\Users\husei\Projects\monas
python -m pip install -r requirements.txt
```

> Jika PC **tidak punya internet**, install wheel offline: download package `.whl` dari mesin yang online, lalu `pip install nama.whl`.

---

## Langkah 6 — Jalankan dashboard

Double-click **`start.bat`** atau di CMD:

```cmd
start.bat
```

Buka browser:
- **Dashboard:** http://localhost:3013
- **API:** http://localhost:8013/docs

Pipeline otomatis:
1. Scan folder `C:\nwp-data\models\`
2. Baca NC → interpolasi ke stasiun
3. Fetch observasi BMKG (POST API, token auto-refresh)
4. Hitung skor HARP D+0 → D+7
5. Tampilkan ranking & grafik

---

## Langkah 7 — Pakai Cursor Local Agent

1. Buka **Cursor Desktop**
2. **File → Open Folder** → pilih `C:\Users\husei\Projects\monas`
3. Buka panel **Chat** (Ctrl+L)
4. Pilih mode **Agent** (bukan Ask)
5. **Pastikan TIDAK memilih "Cloud Agent"** / "Run in cloud"
6. Ketik perintah, contoh:
   ```
   Jalankan pipeline verifikasi untuk file NC terbaru di C:\nwp-data\models\InaNWP\
   ```

Local Agent akan:
- Baca file `C:\Users\husei\...` langsung
- Edit kode & jalankan `python` di PC Anda
- Akses API BMKG intranet `bmkgsatu.bmkg.go.id`

---

## Langkah 8 — Update data rutin

| Data | Cara update |
|------|-------------|
| **Model NC** | SFTP/FileZilla: download file baru dari litbangweb → `C:\nwp-data\models\InaNWP\` → restart dashboard atau tunggu pipeline (1 jam) |
| **Observasi** | Otomatis via BMKG API saat dashboard jalan (token refresh ~47 jam) |

Restart pipeline manual (jika perlu):
```cmd
curl -X POST http://localhost:8013/api/pipeline/run
```

Atau buka: http://localhost:8013/docs → `POST /api/pipeline/run`

---

## Troubleshooting

### Dashboard kosong / "Belum ada skor"
- Pastikan file `.nc` ada di folder `INANWP_NC_PATH`
- Cek: http://localhost:8013/api/pipeline/inventory
- Jalankan pipeline: `POST /api/pipeline/run`

### BMKG API gagal login
- Pastikan PC terhubung **intranet BMKG** (bukan internet publik)
- Cek kredensial di `.env`
- Test: http://localhost:8013/api/bmkg/token-status

### Proses NC 12GB lambat
- Normal — pertama kali bisa 10–30 menit tergantung spesifikasi PC
- Progress: http://localhost:8013/api/jobs/{job_id}

### Port 3013/8013 sudah dipakai
- Ubah di `.env`: `API_PORT=8014`, `FRONTEND_PORT=3014`

---

## Checklist cepat

- [ ] Python 3.11+ terinstall
- [ ] Project di `C:\Users\husei\Projects\monas`
- [ ] Folder `C:\nwp-data\models\InaNWP\` berisi file `.nc`
- [ ] File `.env` sudah diisi username/password BMKG
- [ ] `pip install -r requirements.txt` sukses
- [ ] `start.bat` jalan → http://localhost:3013 terbuka
- [ ] Cursor Local Agent (bukan Cloud) untuk development selanjutnya
