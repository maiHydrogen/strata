"""POST /api/revit: build the native Revit model and IFC for a designed building, then check the IFC.

{"action": "start", "building_file": "<name>.building.json"} -> {"job": id}
{"action": "status", "job": id} -> {"state": queued|running|complete|failed, "report", "text", "files", "ifc_check"}

Revit runs on the STRATA workstation with the STRATA pyRevit listener (revit/STRATA.extension/startup.py);
anywhere else this answers 503 {"local_only": true} and the page shows the manual steps.
"""

import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _http import read, send, use_lib  # noqa: E402

use_lib(prefer_repo=True)
from src.webapi import job_status, revit_status, start_revit  # noqa: E402


class handler(BaseHTTPRequestHandler):  # noqa: N801
    def do_POST(self):  # noqa: N802
        body = read(self, 64_000)
        if body is None:
            return
        action = body.get("action")
        if action == "status":
            st = job_status(str(body.get("job", "")))
            return self._send(200, st) if st else self._send(404, {"error": "no such job"})
        if action != "start":
            return self._send(400, {"error": "action must be start or status"})
        rv = revit_status()
        if not rv.get("ok"):
            return self._send(503, {"local_only": True, "error": rv.get("error") or "Revit is not reachable"})
        if not rv.get("template"):
            return self._send(503, {"error": f"the STRATA Revit template is missing ({rv.get('template_path')})"})
        try:
            self._send(200, {"job": start_revit(str(body.get("building_file", "")))})
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except FileNotFoundError as e:
            self._send(404, {"error": f"no building JSON {e} on the workstation; design the structure first"})

    def _send(self, status, payload):
        send(self, status, payload)
