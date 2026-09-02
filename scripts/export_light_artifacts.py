#!/usr/bin/env python3
"""Export light dashboard artifacts (skor + cache) tanpa NC/forecasts."""
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
    ap.add_argument("--tag", default=None)
    args = ap.parse_args()
    from backend.services.artifacts import export_light_artifacts
    manifest = export_light_artifacts(tag=args.tag)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
