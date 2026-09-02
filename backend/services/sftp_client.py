"""SFTP/local file access for litbangweb server model NC files."""
from __future__ import annotations

import fnmatch
import logging
import os
from pathlib import Path
from typing import Any

import paramiko

from backend.config import (
    DISABLE_SFTP,
    MODEL_LOCAL_PATHS,
    SFTP_HOST,
    SFTP_PASSWORD,
    SFTP_PORT,
    SFTP_USER,
)

# Hindari traceback paramiko di console saat litbangweb tidak reachable
logging.getLogger("paramiko").setLevel(logging.WARNING)


def _is_unix_remote_path(path: str) -> bool:
    p = path.strip().replace("\\", "/")
    return p.startswith("/")


def _sftp_connect() -> paramiko.SFTPClient:
    transport = paramiko.Transport((SFTP_HOST, SFTP_PORT))
    transport.banner_timeout = 15
    transport.connect(username=SFTP_USER, password=SFTP_PASSWORD, timeout=15)
    return paramiko.SFTPClient.from_transport(transport)


def _local_exists(path: str) -> bool:
    return Path(path).exists()


def list_remote_nc_files(remote_path: str, pattern: str = "*.nc") -> list[dict[str, Any]]:
    """
    List NC files — prefer local path (when dashboard runs ON litbangweb server),
    fallback to SFTP listing (unless DISABLE_SFTP).
    """
    results: list[dict[str, Any]] = []

    local = Path(remote_path)
    if local.exists() and local.is_dir():
        for f in sorted(local.glob(pattern)):
            if f.is_file():
                results.append({
                    "filename": f.name,
                    "path": str(f),
                    "size": f.stat().st_size,
                    "accessible": True,
                    "source": "local",
                })
        return results

    if DISABLE_SFTP:
        return results

    # Path Linux di PC Windows tanpa folder lokal — skip SFTP jika path server-only
    if os.name == "nt" and _is_unix_remote_path(remote_path) and not local.exists():
        return results

    try:
        sftp = _sftp_connect()
        for attr in sftp.listdir_attr(remote_path):
            if not attr.filename.endswith(".nc"):
                continue
            if not fnmatch.fnmatch(attr.filename, pattern):
                continue
            is_dir = (attr.st_mode & 0o170000) == 0o040000
            if is_dir:
                continue
            results.append({
                "filename": attr.filename,
                "path": f"{remote_path.rstrip('/')}/{attr.filename}",
                "size": attr.st_size,
                "accessible": False,
                "source": "sftp",
            })
        sftp.close()
    except Exception as e:
        print(f"[sftp] skip list {remote_path}: {e}")

    return results


def resolve_model_nc_path(nc_path: str) -> Path:
    """
    Resolve NC path for reading.
    - If local file exists (dashboard on litbangweb): use directly (no download).
    - If SFTP remote path: download to cache if not already cached.
    """
    p = Path(nc_path)
    if p.exists():
        return p

    if DISABLE_SFTP:
        raise FileNotFoundError(
            f"File NC tidak ditemukan lokal: {nc_path}. "
            "Set LOCAL_NC_PATH / INANWP_NC_PATH atau DISABLE_SFTP=false untuk download SFTP."
        )

    # SFTP remote — check cache
    from backend.config import NC_DIR
    cache = NC_DIR / Path(nc_path).name
    if cache.exists() and cache.stat().st_size > 0:
        return cache

    # Download from SFTP (large files — only when necessary)
    sftp = _sftp_connect()
    try:
        NC_DIR.mkdir(parents=True, exist_ok=True)
        sftp.get(nc_path, str(cache))
    finally:
        sftp.close()
    return cache


def get_server_model_inventory() -> dict[str, Any]:
    """Return inventory of all model NC files on litbangweb."""
    inventory = {}
    for model, cfg in MODEL_LOCAL_PATHS.items():
        files = list_remote_nc_files(cfg["path"], cfg.get("pattern", "*.nc"))
        inventory[model] = {
            "path": cfg["path"],
            "files": files,
            "count": len(files),
            "local_access": any(f.get("source") == "local" for f in files),
        }
    return inventory
