"""Export/import observasi Sinoptik — PC lokal → litbangweb via SFTP."""
from __future__ import annotations

import json
import stat
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend.config import (
    OBS_EXPORT_DIR,
    SFTP_HOST,
    SFTP_OBS_PATH,
    SFTP_PASSWORD,
    SFTP_PORT,
    SFTP_USER,
)
from backend.services.bmkg_export import fetch_sinoptik_range
from backend.services.obs_fetcher import normalize_obs_records, save_observations, upsert_stations_from_records

# Fallback path di folder wrf (biasanya writable user litbangweb, sama ekosistem psiidn)
SFTP_OBS_FALLBACK = "/opt/lampp/htdocs/wrf/monas_obs"


def _export_dir() -> Path:
    d = Path(OBS_EXPORT_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sftp_makedirs(sftp: Any, remote_path: str) -> None:
    """Buat folder remote rekursif; raise jika gagal."""
    parts = remote_path.strip("/").split("/")
    cur = ""
    for part in parts:
        cur = f"{cur}/{part}"
        try:
            st = sftp.stat(cur)
            if stat.S_ISDIR(st.st_mode):
                continue
            raise OSError(f"Bukan folder: {cur}")
        except OSError as e:
            if "No such file" in str(e) or getattr(e, "errno", None) == 2:
                try:
                    sftp.mkdir(cur)
                except OSError as mkdir_err:
                    raise OSError(
                        f"Tidak bisa buat folder remote {cur}: {mkdir_err}. "
                        f"Buat manual via SSH: mkdir -p {remote_path}"
                    ) from mkdir_err
            else:
                raise


def _connect_sftp() -> Any:
    import paramiko

    transport = paramiko.Transport((SFTP_HOST, SFTP_PORT))
    transport.connect(username=SFTP_USER, password=SFTP_PASSWORD)
    return paramiko.SFTPClient.from_transport(transport), transport


async def fetch_and_export_sinoptik(
    date_from: str,
    date_to: str,
    out_dir: Path | None = None,
    also_save_db: bool = False,
) -> dict[str, Any]:
    """
    Fetch BMKG Sinoptik API v21 → simpan JSON ke folder lokal (D: drive).
    API sesuai PPTX: POST .../export/observation/by-station/query, chunk ≤4 hari.
    """
    out_dir = out_dir or _export_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    records = await fetch_sinoptik_range(date_from, date_to)
    tag_from = date_from[:10].replace("-", "")
    tag_to = date_to[:10].replace("-", "")
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"sinoptik_{tag_from}_{tag_to}_{ts}.json"
    out_path = out_dir / filename

    payload = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "date_from": date_from,
        "date_to": date_to,
        "record_count": len(records),
        "data": records,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result: dict[str, Any] = {
        "file": str(out_path),
        "records": len(records),
        "date_from": date_from,
        "date_to": date_to,
    }

    if records == 0:
        result["warning"] = (
            "0 records — coba: python scripts/fetch_obs_local.py --test-api "
            "atau rentang tanggal lain (--from 2026-07-01 --to 2026-07-05)"
        )

    if also_save_db and records:
        df = normalize_obs_records(records)
        result["db_saved"] = save_observations(df)
        upsert_stations_from_records(records)

    return result


def _parse_iso_dt(s: str) -> datetime:
    s = s.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")


def default_obs_fetch_start() -> str:
    """1 Juni tahun berjalan (UTC), atau OBS_FETCH_START dari .env."""
    from backend.config import OBS_FETCH_START

    if OBS_FETCH_START:
        return OBS_FETCH_START if "T" in OBS_FETCH_START else f"{OBS_FETCH_START}T00:00:00Z"
    year = datetime.utcnow().year
    return f"{year}-06-01T00:00:00Z"


def _month_ranges(date_from: datetime, date_to: datetime) -> list[tuple[datetime, datetime]]:
    """Pecah rentang menjadi per bulan kalender (inklusif)."""
    ranges: list[tuple[datetime, datetime]] = []
    cursor = date_from.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if cursor < date_from:
        cursor = date_from.replace(hour=0, minute=0, second=0, microsecond=0)

    while cursor <= date_to:
        if cursor.month == 12:
            next_month = cursor.replace(year=cursor.year + 1, month=1, day=1)
        else:
            next_month = cursor.replace(month=cursor.month + 1, day=1)
        chunk_end = min(next_month - timedelta(seconds=1), date_to)
        chunk_start = max(cursor, date_from)
        if chunk_start <= chunk_end:
            ranges.append((chunk_start, chunk_end))
        cursor = next_month
    return ranges


async def fetch_and_export_sinoptik_monthly(
    date_from: str,
    date_to: str,
    out_dir: Path | None = None,
    also_save_db: bool = False,
) -> dict[str, Any]:
    """
    Fetch observasi per bulan kalender → satu JSON per bulan.
    Lebih aman untuk rentang panjang (Juni → sekarang) dan hindari gap di grafik.
    """
    start = _parse_iso_dt(date_from)
    end = _parse_iso_dt(date_to)
    out_dir = out_dir or _export_dir()
    files: list[dict[str, Any]] = []
    total_records = 0
    total_db = 0

    ranges = _month_ranges(start, end)
    print(f"  Fetch {len(ranges)} bulan: {date_from[:10]} → {date_to[:10]}")

    for i, (chunk_start, chunk_end) in enumerate(ranges, 1):
        cf = chunk_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        ct = chunk_end.strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"  [{i}/{len(ranges)}] {cf[:10]} → {ct[:10]} ...")
        result = await fetch_and_export_sinoptik(cf, ct, out_dir=out_dir, also_save_db=also_save_db)
        files.append(result)
        total_records += result.get("records", 0)
        total_db += result.get("db_saved", 0)
        if result.get("warning"):
            print(f"    WARNING: {result['warning']}")

    return {
        "files": files,
        "months": len(files),
        "records": total_records,
        "db_saved": total_db,
        "date_from": date_from,
        "date_to": date_to,
    }


def import_obs_from_json_dir(
    directory: str | Path | None = None,
    pattern: str = "sinoptik_*.json",
    clear_existing: bool = False,
    recent_days: int | None = None,
) -> dict[str, Any]:
    """Import file JSON observasi ke SQLite (untuk litbangweb / pipeline).

    recent_days: jika diisi, skip file yang rentang tanggalnya (dari nama
    sinoptik_YYYYMMDD_YYYYMMDD_*) berakhir sebelum (hari ini − recent_days).
    Ini menghindari import 3×~400MB bulanan penuh saat verify harian.
    """
    import os
    import re
    from datetime import datetime, timedelta

    directory = Path(directory or SFTP_OBS_PATH)
    if not directory.is_dir():
        return {"files": 0, "records_saved": 0, "error": f"Folder tidak ada: {directory}"}

    if recent_days is None:
        raw = os.getenv("OBS_IMPORT_RECENT_DAYS", "").strip()
        recent_days = int(raw) if raw.isdigit() else 0

    if clear_existing:
        from backend.services.obs_fetcher import clear_verification_data
        cleared = clear_verification_data()
    else:
        cleared = {}

    cutoff = None
    if recent_days and recent_days > 0:
        cutoff = (datetime.utcnow() - timedelta(days=recent_days)).strftime("%Y%m%d")
        print(f"[obs] Filter recent_days={recent_days} (end_date >= {cutoff})", flush=True)

    total_saved = 0
    files_loaded = 0
    files_skipped = 0
    paths = sorted(directory.glob(pattern))
    selected: list[Path] = []
    for path in paths:
        if cutoff:
            m = re.match(r"sinoptik_(\d{8})_(\d{8})", path.name)
            if m:
                end = m.group(2)
                if end < cutoff:
                    files_skipped += 1
                    print(f"[obs]   skip lama: {path.name} (end {end} < {cutoff})", flush=True)
                    continue
        selected.append(path)

    n_files = len(selected)
    print(
        f"[obs] Import {n_files} file dari {directory} "
        f"(pattern={pattern}, skipped_old={files_skipped})",
        flush=True,
    )
    for i, path in enumerate(selected, 1):
        size_mb = path.stat().st_size / 1e6
        print(f"[obs]   [{i}/{n_files}] baca {path.name} ({size_mb:.0f} MB)...", flush=True)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[obs]   skip {path.name}: {e}", flush=True)
            continue
        records = raw if isinstance(raw, list) else raw.get("data", [])
        from backend.services.obs_format import flatten_sinoptik_records
        records = flatten_sinoptik_records(records)
        if not records:
            print(f"[obs]   [{i}/{n_files}] {path.name}: 0 records", flush=True)
            continue
        df = normalize_obs_records(records)
        n = save_observations(df)
        upsert_stations_from_records(records)
        total_saved += n
        files_loaded += 1
        print(f"[obs]   [{i}/{n_files}] {path.name}: +{n} rows (total {total_saved})", flush=True)

    result = {
        "files": files_loaded,
        "records_saved": total_saved,
        "directory": str(directory),
        "skipped_old": files_skipped,
        "recent_days": recent_days or 0,
    }
    if cleared:
        result["cleared"] = cleared
    print(f"[obs] Selesai: {result}", flush=True)
    return result


def upload_obs_exports_to_litbangweb(
    local_dir: str | Path | None = None,
    remote_path: str | None = None,
    pattern: str = "sinoptik_*.json",
    min_records: int = 1,
    files_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Upload file JSON observasi dari PC lokal ke litbangweb via SFTP (port 3346)."""
    local_dir = Path(local_dir or OBS_EXPORT_DIR)
    remote_path = (remote_path or SFTP_OBS_PATH).rstrip("/")

    if not local_dir.is_dir():
        raise FileNotFoundError(f"Folder lokal tidak ada: {local_dir}")

    if files_filter:
        files = [local_dir / f for f in files_filter if (local_dir / f).is_file()]
    else:
        files = sorted(local_dir.glob(pattern))

    if not files:
        return {"uploaded": 0, "message": f"Tidak ada file {pattern} di {local_dir}"}

    # Skip file kosong kecuali explicitly filtered
    if min_records > 0 and not files_filter:
        non_empty = []
        for f in files:
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
                n = raw.get("record_count", len(raw.get("data", [])))
                if n >= min_records:
                    non_empty.append(f)
                else:
                    print(f"  skip (kosong): {f.name} — record_count={n}")
            except (json.JSONDecodeError, OSError):
                pass
        files = non_empty

    if not files:
        return {
            "uploaded": 0,
            "message": "Semua file JSON kosong (0 records). Perbaiki fetch BMKG dulu.",
        }

    sftp, transport = _connect_sftp()
    uploaded = []
    errors = []

    for try_path in (remote_path, SFTP_OBS_FALLBACK):
        try:
            _sftp_makedirs(sftp, try_path)
            for local_file in files:
                remote_file = f"{try_path}/{local_file.name}"
                sftp.put(str(local_file), remote_file)
                uploaded.append({
                    "local": local_file.name,
                    "remote": remote_file,
                    "size": local_file.stat().st_size,
                })
            remote_path = try_path
            break
        except OSError as e:
            errors.append(f"{try_path}: {e}")
            uploaded.clear()
    else:
        sftp.close()
        transport.close()
        raise OSError(
            "Upload SFTP gagal ke semua path.\n"
            + "\n".join(errors)
            + "\n\nBuat folder manual di litbangweb (SSH):\n"
            f"  mkdir -p {remote_path}\n"
            f"  # atau: mkdir -p {SFTP_OBS_FALLBACK}\n"
            "Lalu set SFTP_OBS_PATH di .env ke path yang berhasil."
        )

    sftp.close()
    transport.close()
    return {"uploaded": len(uploaded), "remote_path": remote_path, "files": uploaded}


def fetch_export_and_upload(days: int = 10) -> dict[str, Any]:
    """Satu langkah: fetch BMKG → simpan D: → upload ke litbangweb."""
    import asyncio

    end = datetime.utcnow()
    start = end - timedelta(days=days)
    date_from = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_to = end.strftime("%Y-%m-%dT%H:%M:%SZ")

    export_result = asyncio.run(fetch_and_export_sinoptik(date_from, date_to, also_save_db=True))
    if export_result.get("records", 0) == 0:
        return {"export": export_result, "upload": {"skipped": True, "reason": "0 records"}}
    upload_result = upload_obs_exports_to_litbangweb(
        files_filter=[Path(export_result["file"]).name],
    )
    return {"export": export_result, "upload": upload_result}
