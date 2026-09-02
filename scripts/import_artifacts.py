#!/usr/bin/env python3
"""Import light artifacts ke DB lokal (jalankan di webpsi setelah sync)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--from", dest="source", default="data/artifacts/latest",
        help="Path ke folder artifact atau dashboard.sqlite",
    )
    args = ap.parse_args()
    from backend.services.artifacts import import_light_artifacts
    result = import_light_artifacts(args.source)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
