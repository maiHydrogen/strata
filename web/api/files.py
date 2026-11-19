"""GET /api/files?name=<name>.rvt|.ifc|.building.json: download a model file from the workstation's shared
folder (bare names with those extensions only; access code required)."""

import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _http import allowed, send, use_lib  # noqa: E402

use_lib(prefer_repo=True)
from src.webapi import safe_file  # noqa: E402


class handler(BaseHTTPRequestHandler):  # noqa: N801
    def do_GET(self):  # noqa: N802
        if not allowed(self):
            return
        name = (parse_qs(urlparse(self.path).query).get("name") or [""])[0]
        path = safe_file(name)
        if path is None:
            return send(self, 404, {"error": "no such file"})
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                self.wfile.write(chunk)

    def _send(self, status, payload):
        send(self, status, payload)
