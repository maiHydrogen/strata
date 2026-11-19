"""POST /api/spec: the cited design spec of a confirmed brief (knowledge graph, no LLM; runs anywhere).

Request {"brief": {...}}  Response: requirements grouped by room and building part, each with its
clause and page, the plot checks, what was assumed, and what could not be resolved.
"""

import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _http import read, send, use_lib  # noqa: E402

use_lib()
from src.webapi import spec_view  # noqa: E402


class handler(BaseHTTPRequestHandler):  # noqa: N801
    def do_POST(self):  # noqa: N802
        body = read(self, 64_000)
        if body is None:
            return
        try:
            self._send(200, spec_view(body["brief"]))
        except (KeyError, ValueError, TypeError) as e:
            self._send(400, {"error": f"could not read the brief ({type(e).__name__})"})

    def _send(self, status, payload):
        send(self, status, payload)
