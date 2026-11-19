"""POST /api/structure: design the structure for an approved layout and build the building JSON.

{"action": "start", "brief": {...}, "layout": {...}} -> {"job": id}
{"action": "status", "job": id} -> {"state": running|complete|stopped|failed, "rounds": [...], "result": {...}}

STAAD runs on the STRATA workstation, so this works only where the API runs there; elsewhere it
answers 503 {"local_only": true} and the pages show the saved demo run.
"""

import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _http import REPO, read, send, use_lib  # noqa: E402

use_lib(prefer_repo=True)
from src.webapi import job_status, staad_available, start_job  # noqa: E402


class handler(BaseHTTPRequestHandler):  # noqa: N801
    def do_POST(self):  # noqa: N802
        body = read(self, 512_000)
        if body is None:
            return
        action = body.get("action")
        if action == "status":
            st = job_status(str(body.get("job", "")))
            return self._send(200, st) if st else self._send(404, {"error": "no such job"})
        if action != "start":
            return self._send(400, {"error": "action must be start or status"})
        if not staad_available():
            return self._send(503, {"local_only": True,
                                    "error": "STAAD.Pro runs on the STRATA workstation; this server cannot reach it"})
        try:
            runs = os.path.join(REPO, "output", "runs") if os.path.isdir(os.path.join(REPO, "src")) else None
            self._send(200, {"job": start_job(body["brief"], body["layout"], out_root=runs)})
        except (KeyError, ValueError, TypeError) as e:
            self._send(400, {"error": f"could not read the brief or layout ({type(e).__name__}: {e})"})

    def _send(self, status, payload):
        send(self, status, payload)
