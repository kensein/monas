# Agent Brief — MONAS

Handoff untuk **agent baru**. Baca ini dulu, lalu `README.md` + `docs/DEPLOY_LITBANGWEB_WEBPSI.md`.

---

## Satu kalimat

MONAS = dashboard HARP point-verification **display-only** di `psimkg.bmkg.go.id/monas/`:  
hitung di **litbangweb (Docker, offline)** → artifact **f32** → **webpsi** `SERVE_READONLY`.

---

## Jangan ulangi kesalahan lama

| Salah | Benar |
|-------|--------|
| Hitung HARP di webpsi / browser | Hitung di litbangweb; webpsi hanya baca f32 |
| Baca wrfout 12GB langsung | Crop CDO/ncks → `monas_nc/` (~300MB, `lev=0`) |
| litbangweb **push** ke webpsi:22 | webpsi/PC **pull** SFTP litbangweb `:3346` |
| Fallback Tmax/Tmin/wbpt → `t2m` | Tandai “tidak di NC”; jangan nilai palsu |
| Set obs `8888`/`9999` → `0` | **Buang** (NaN) — lihat `backend/services/obs_qc.py` |
| Rewrite UI ke React demi latency | Precompute + f32; UI tetap Canvas |
| Kunci semua parameter jika API tanpa `available_by_model` | Fail-open di frontend |

---

## Mesin & path

| Mesin | Path / peran |
|-------|----------------|
| litbangweb | `202.90.199.54:3346` · wrfout → `monas_nc` · obs `monas_obs` · export `monas_export` · Docker `monas-compute` |
| webpsi | `/var/www/monas` · PM2 `monas-api` (:8013) + `monas-web` (:3013) · URL `/monas/` |
| PC BMKG | Fetch obs BMKG API → SFTP `monas_obs` · `scripts/daily_obs_pc.bat` |

Env kritis webpsi: `SERVE_READONLY=true`, `STORE_BACKEND=f32`, `BASE_PATH=/monas`, `CARTO_API_KEY=...`

---

## Kode yang penting

| Area | Lokasi |
|------|--------|
| API | `backend/main.py` |
| Config / param / model paths | `backend/config.py` |
| HARP compute | `backend/services/harp_compute.py`, `verification.py` |
| f32 I/O | `backend/services/harp_store.py` |
| Obs sentinel QC | `backend/services/obs_qc.py` |
| UI | `frontend/app.js`, `index.html`, `canvas-*.js` |
| Docker compute | `docker/compute/` |
| Cron / pull | `scripts/litbangweb_daily_compute.sh`, `pull_artifacts_from_litbangweb.sh`, `crop_inanwp_cdo.sh` |

---

## Fitur UI yang sudah ada

- Tab: Overview, Scores vs Lead (dropdown metrik), Peta, Metode, Detail Stasiun  
- Lead: slider + prev / play / next (Overview & Peta)  
- Detail stasiun: zoom/pan chart; legend = nama model; gaya beda per init  
- Parameter disabled hanya jika model tercentang & benar-benar tidak di NC  

---

## Setelah ubah kode

1. Branch `cursor/<nama>-3ba0` → PR → **merge ke `main`** (user sering minta merge langsung).  
2. webpsi: `cd /var/www/monas && git pull && pm2 restart monas-api monas-web --update-env`  
3. Jika ubah compute: rebuild/load image di litbangweb + jalankan ulang job (+ `--force` bila perlu).  
4. Hard-refresh browser (`Ctrl+Shift+R`).

---

## Backlog yang sudah dibahas (belum dikerjakan)

1. **Bias correction** (MBR / linear MOS dulu, bukan ML) — butuh historis panjang; data InaNWP baru masih pendek (Sept). Archive 2023+ hanya jika ada NC + obs berpasangan.  
2. Model real selain InaNWP.  
3. Panel N-cases vs lead seperti harpR.

---

## Prompt awal yang disarankan

> Lanjut MONAS. Baca `README.md` dan `AGENT_BRIEF.md`. Arsitektur: PC obs → litbangweb Docker f32 → webpsi SERVE_READONLY. Jangan hitung di webpsi. Ikuti QC sentinel 8888/9999 (buang, bukan 0). Merge & push ke main jika diminta.

---

*Sync dengan README arsitektur 2026-09-13.*
