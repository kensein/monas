#!/usr/bin/env python3
"""Serve frontend static files (dev lokal atau PM2 production dengan BASE_PATH)."""
from __future__ import annotations

import http.server
import os
import socketserver
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT / "frontend"
PORT = int(os.getenv("FRONTEND_PORT", os.getenv("PORT", "3013")))
HOST = os.getenv("FRONTEND_HOST", os.getenv("HOSTNAME", "127.0.0.1"))
BASE_PATH = os.getenv("BASE_PATH", "").rstrip("/")


class SubpathHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def _strip_base(self, path: str) -> str:
        path = unquote(path.split("?", 1)[0])
        if BASE_PATH and path.startswith(BASE_PATH):
            path = path[len(BASE_PATH):] or "/"
        return path

    def translate_path(self, path: str) -> str:
        path = self._strip_base(path)
        if path in ("", "/"):
            path = "/index.html"
        rel = path.lstrip("/")
        return str(FRONTEND_DIR / rel)

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()


class ReuseTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


with ReuseTCPServer((HOST, PORT), SubpathHandler) as httpd:
    prefix = BASE_PATH or "/"
    print(f"Frontend: http://{HOST}:{PORT}{prefix}")
    httpd.serve_forever()
