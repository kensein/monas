#!/usr/bin/env python3
r"""
Diagnostik observasi: stasiun hash vs WMO, rentang tanggal, file JSON.

Contoh:
  python scripts/check_obs_coverage.py
  python scripts/check_obs_coverage.py --station 96011
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def _is_wmo_id(sid: str) -> bool:
    return str(sid).isdigit() and len(str(sid)) == 5


def main() -> None:
    parser = argparse.ArgumentParser(description="Cek coverage obs vs katalog WMO")
    parser.add_argument("--station", help="Filter satu station_id (WMO atau hash)")
    parser.add_argument("--json-dir", help="Scan rentang tanggal di file JSON (default OBS_EXPORT_DIR)")
    parser.add_argument("--hash-only", action="store_true", help="Tampilkan semua stasiun hash (full list)")
    parser.add_argument("--catalog-unused", action="store_true", help="Katalog WMO tanpa data obs di DB")
    parser.add_argument("--export", metavar="FILE", help="Export CSV: hash-only → hash; catalog-unused → unused")
    args = parser.parse_args()

    from backend.config import OBS_EXPORT_DIR
    from backend.services.obs_fetcher import get_db, init_db
    from backend.services.station_catalog import catalog_to_dataframe, load_station_catalog, lookup_wmo_by_name

    init_db()
    conn = get_db()

    print("=== Katalog stasiun ===")
    print(f"  Entri katalog : {len(load_station_catalog())}")

    print("\n=== Station ID di database observasi ===")
    q = """
        SELECT station_id,
               COUNT(*) AS n_rows,
               MIN(valid_time) AS t_min,
               MAX(valid_time) AS t_max
        FROM observations
        GROUP BY station_id
        ORDER BY station_id
    """
    rows = conn.execute(q).fetchall()

    wmo_stations, hash_stations = [], []
    for r in rows:
        sid = r["station_id"]
        if args.station and sid != args.station:
            continue
        info = {
            "station_id": sid,
            "n_rows": r["n_rows"],
            "t_min": r["t_min"],
            "t_max": r["t_max"],
        }
        st = conn.execute(
            "SELECT name FROM stations WHERE station_id = ?", (sid,)
        ).fetchone()
        info["name"] = st["name"] if st else "(nama tidak di stations table)"
        if _is_wmo_id(sid):
            wmo_stations.append(info)
        else:
            hash_stations.append(info)

    hash_stations.sort(key=lambda x: -x["n_rows"])
    wmo_stations.sort(key=lambda x: x["station_id"])
    print(f"  Pakai WMO (5 digit) : {len(wmo_stations)}  → ikut verifikasi jika ada pasangan fcst")
    print(f"  Pakai hash ID       : {len(hash_stations)}  → TIDAK ikut verifikasi")

    catalog_df = catalog_to_dataframe()
    obs_wmo_ids = {s["station_id"] for s in wmo_stations}
    catalog_wmo_ids = set(catalog_df["station_id"].astype(str))
    unused_catalog = catalog_df[~catalog_df["station_id"].astype(str).isin(obs_wmo_ids)]

    print(f"\n=== Penjelasan angka ===")
    print(f"  Katalog {len(catalog_df)} = stasiun dengan WMO+koordinat (Matriks UPT)")
    print(f"  Obs WMO {len(wmo_stations)} = stasiun yang PUNYA data sinoptik Juni–Sep di DB")
    print(f"  Katalog tanpa obs     : {len(unused_catalog)}  (normal — tidak semua mengirim/tidak aktif)")
    print(f"  Obs hash {len(hash_stations)} = nama BMKG tidak match katalog (Geofisika/TNIAU/dll.)")
    print(f"  Total unik di obs     : {len(wmo_stations) + len(hash_stations)} = WMO + hash")

    if hash_stations and (args.hash_only or not args.station):
        limit = len(hash_stations) if args.hash_only else 30
        print(f"\n--- Stasiun HASH ({'semua' if args.hash_only else f'{limit} pertama'}) ---")
        for s in hash_stations[:limit]:
            wmo_hint = lookup_wmo_by_name(s["name"])
            hint = f"  → coba WMO {wmo_hint}" if wmo_hint else ""
            print(f"  {s['station_id']}  {s['name'][:55]}{hint}")
            print(f"      baris={s['n_rows']:,}  {s['t_min'][:10]} → {s['t_max'][:10]}")
        if not args.hash_only and len(hash_stations) > 30:
            print(f"  ... +{len(hash_stations) - 30} stasiun lagi")
            print(f"  Full list: python scripts\\check_obs_coverage.py --hash-only")
            print(f"  Export CSV: python scripts\\check_obs_coverage.py --hash-only --export hash_stations.csv")

    if args.catalog_unused and not args.station:
        print(f"\n--- Katalog WMO TANPA data obs ({len(unused_catalog)}) ---")
        for _, row in unused_catalog.sort_values("station_id").iterrows():
            print(f"  {row['station_id']}  {str(row['name'])[:55]}")

    if args.export:
        import csv
        out = Path(args.export)
        if args.catalog_unused or "unused" in out.stem:
            rows = unused_catalog[["station_id", "name", "lat", "lon", "region"]].to_dict("records")
            fields = ["station_id", "name", "lat", "lon", "region"]
        else:
            rows = [{**s, "wmo_hint": lookup_wmo_by_name(s["name"]) or ""} for s in hash_stations]
            fields = ["station_id", "name", "n_rows", "t_min", "t_max", "wmo_hint"]
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"\nExported {len(rows)} baris → {out.resolve()}")

    if wmo_stations and not args.station and not args.hash_only:
        print(f"\n--- Sample stasiun WMO (10 pertama) ---")
        for s in wmo_stations[:10]:
            print(f"  {s['station_id']}  {s['name'][:45]}  {s['t_min'][:10]} → {s['t_max'][:10]}")

    if args.station:
        print(f"\n=== Detail stasiun {args.station} ===")
        one = wmo_stations + hash_stations
        if one:
            s = one[0]
            print(json.dumps(s, indent=2, ensure_ascii=False))
            # Hitung per bulan
            months = conn.execute(
                """SELECT substr(valid_time,1,7) AS ym, COUNT(*) AS n
                   FROM observations WHERE station_id = ?
                   GROUP BY ym ORDER BY ym""",
                (args.station,),
            ).fetchall()
            print("\n  Baris per bulan:")
            for m in months:
                bar = "#" * min(40, m["n"] // 50)
                print(f"    {m['ym']}  {m['n']:6,}  {bar}")
        else:
            print("  Tidak ada data observasi untuk station_id ini.")

    conn.close()

    json_dir = Path(args.json_dir or OBS_EXPORT_DIR)
    if json_dir.is_dir():
        print(f"\n=== File JSON di {json_dir} ===")
        for p in sorted(json_dir.glob("sinoptik_*.json")):
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                print(f"  {p.name}  (gagal baca)")
                continue
            data = raw.get("data", raw) if isinstance(raw, dict) else raw
            n = raw.get("record_count", len(data) if isinstance(data, list) else 0)
            df = raw.get("date_from", "?")
            dt = raw.get("date_to", "?")
            # Sample timestamp dari baris pertama
            t_sample = "?"
            if isinstance(data, list) and data:
                row = data[0]
                if isinstance(row, dict):
                    t_sample = row.get("data_timestamp", "?")
                elif isinstance(row, list) and len(row) > 1:
                    t_sample = row[1]
            print(f"  {p.name}")
            print(f"      meta: {df} → {dt}  records={n}")
            print(f"      sample ts: {t_sample}")

    print("\n=== Cara baca ===")
    print("  • Hash ID = nama stasiun tidak ketemu di katalog → perlu tambah ke stations_bmkg.json")
    print("  • Gap kosong di grafik = tidak ada file obs untuk rentang itu ATAU tidak ada fcst di init cycle")
    print("  • Forecast hanya ada untuk init cycle NC (mis. 2026070112 → ~1–8 Juli 2026)")


if __name__ == "__main__":
    main()
