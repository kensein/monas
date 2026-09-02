#!/usr/bin/env python3
"""Backfill verification_station_scores + ranking_cache dari data SQLite yang sudah ada."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.verification_cache import rebuild_dashboard_cache


def main() -> None:
    print("Rebuilding dashboard cache (station scores + ranking)...")
    result = rebuild_dashboard_cache()
    print(f"Done: {result['station_rows']} station rows, {result['ranking_keys']} ranking keys cached")


if __name__ == "__main__":
    main()
