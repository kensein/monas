#!/usr/bin/env python3
"""Import katalog stasiun BMKG dari Excel Matriks UPT → data/stations_bmkg.json."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="Import metadata stasiun BMKG dari Excel")
    parser.add_argument(
        "xlsx",
        nargs="?",
        default=str(ROOT / "data" / "Matriks_Kesiapan_Otomatisasi_UPT.xlsx"),
        help="Path ke file Excel (sheet 'meta data stasiun')",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=str(ROOT / "backend" / "resources" / "stations_bmkg.json"),
        help="Output JSON",
    )
    args = parser.parse_args()

    try:
        import pandas as pd
    except ImportError as e:
        raise SystemExit("pandas required") from e
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        raise SystemExit("Install openpyxl: pip install openpyxl")

    src = Path(args.xlsx)
    if not src.is_file():
        raise SystemExit(f"File tidak ditemukan: {src}")

    df = pd.read_excel(src, sheet_name="meta data stasiun")
    df = df[["WMO_ID", "NAME", "PROPINSI_NAME", "LAT", "LON", "ELEV", "TYPE_MKG"]]
    df = df.dropna(subset=["WMO_ID", "NAME", "LAT", "LON"])

    records = []
    for _, row in df.iterrows():
        wmo = str(int(row["WMO_ID"]))
        records.append({
            "wmo_id": wmo,
            "name": str(row["NAME"]).strip(),
            "region": str(row["PROPINSI_NAME"]).strip() if pd.notna(row["PROPINSI_NAME"]) else "",
            "lat": float(row["LAT"]),
            "lon": float(row["LON"]),
            "elevation": float(row["ELEV"]) if pd.notna(row["ELEV"]) else None,
            "type_mkg": str(row["TYPE_MKG"]).strip() if pd.notna(row["TYPE_MKG"]) else "",
        })

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: {len(records)} stasiun → {out}")

    from backend.services.station_catalog import invalidate_catalog_cache, sync_catalog_to_db
    from backend.services.obs_fetcher import init_db

    invalidate_catalog_cache()
    init_db()
    n = sync_catalog_to_db(force=True)
    print(f"DB sync: {n} stasiun")


if __name__ == "__main__":
    main()
