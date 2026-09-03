# Flow cadangan: hitung di PC → webpsi

> **Preferensi produksi:** hitung di **litbangweb (Docker)** — lihat `DEPLOY_LITBANGWEB_WEBPSI.md`.  
> Dokumen ini untuk cadangan jika litbangweb belum siap.

## Peran singkat

| Mesin | Tugas |
|-------|--------|
| PC | Obs + (opsional) verify + export |
| webpsi | `SERVE_READONLY` tampilkan artifact |

PC obs harian tetap dipakai meski compute di litbangweb:
```bat
scripts\daily_obs_pc.bat
```

## PC verify (cadangan)

```bat
scripts\daily_verify_pc.bat --skip-obs --skip-sync
python scripts\export_light_artifacts.py
```

Lalu SCP `data\artifacts\latest` ke webpsi dan import.

Detail webpsi: `DEPLOY_MONAS.md` + `SERVE_READONLY=true`.
