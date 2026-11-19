"""POST /api/ifc: check Revit's IFC export against the building JSON (IfcOpenShell; workstation only).

Request {"building": {...}, "ifc_base64": "..."}  Response {"schema", "passed", "checks", "info"}
"""

import base64
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _http import read, send, use_lib  # noqa: E402

use_lib(prefer_repo=True)


class handler(BaseHTTPRequestHandler):  # noqa: N801
    def do_POST(self):  # noqa: N802
        body = read(self, 80_000_000)
        if body is None:
            return
        try:
            from src.webapi import ifc_view
        except ImportError:
            return self._send(503, {"local_only": True, "error": "IfcOpenShell is not installed on this server"})
        try:
            self._send(200, ifc_view(base64.b64decode(body["ifc_base64"]), body["building"]))
        except (KeyError, ValueError, TypeError) as e:
            self._send(400, {"error": f"could not read the IFC or building ({type(e).__name__})"})
        except RuntimeError as e:  # IfcOpenShell cannot parse the file
            self._send(400, {"error": f"not a readable IFC file ({e})"})

    def _send(self, status, payload):
        send(self, status, payload)
