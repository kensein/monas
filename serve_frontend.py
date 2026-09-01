#!/usr/bin/env python3
"""Serve frontend static files on port 3013."""
import http.server
import os
import socketserver

PORT = int(os.getenv("FRONTEND_PORT", "3013"))
DIR = os.path.join(os.path.dirname(__file__), "frontend")
os.chdir(DIR)

class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

with socketserver.TCPServer(("0.0.0.0", PORT), Handler) as httpd:
    print(f"Frontend: http://0.0.0.0:{PORT}")
    httpd.serve_forever()
