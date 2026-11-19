"""GET /api/health: is this the STRATA workstation, and are STAAD and Revit up (no access code needed;
it says nothing private). On Vercel it answers {"workstation": false}."""

import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _http import send, use_lib  # noqa: E402

use_lib(prefer_repo=True)


class handler(BaseHTTPRequestHandler):  # noqa: N801
    def do_GET(self):  # noqa: N802
        try:
            from src.webapi import health, staad_available
            out = health() if staad_available() else {"ok": True, "workstation": False}
        except Exception as e:  # noqa: BLE001 - a hosted copy without the workstation packages
            out = {"ok": True, "workstation": False, "error": type(e).__name__}
        send(self, 200, out)

    def _send(self, status, payload):
        send(self, status, payload)
