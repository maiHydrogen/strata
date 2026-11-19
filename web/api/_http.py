"""Shared bits of the STRATA API handlers (files starting with _ are not routes on Vercel)."""

import json
import os
import sys

API = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(API))  # the STRATA repo, when running on the workstation


def use_lib(prefer_repo=False):
    """Put the STRATA Python code on the path: the repo itself on the workstation (local-only routes, so
    output lands in the repo's output/), else the copy in api/_lib (Vercel)."""
    repo_src = os.path.join(REPO, "src", "orchestrator.py")
    path = REPO if prefer_repo and os.path.exists(repo_src) else os.path.join(API, "_lib")
    if path not in sys.path:
        sys.path.insert(0, path)


def allowed(handler):
    """True if no access code is set or the request carries it; else answers 401."""
    code = os.environ.get("STRATA_ACCESS_CODE")
    if code and handler.headers.get("X-Strata-Code") != code:
        send(handler, 401, {"error": "access code required"})
        return False
    return True


def read(handler, limit):
    if not allowed(handler):
        return None
    length = int(handler.headers.get("Content-Length") or 0)
    if length > limit:
        handler._send(413, {"error": "request too large"})
        return None
    return json.loads(handler.rfile.read(length) or b"{}")


def send(handler, status, payload):
    data = json.dumps(payload, default=str).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)
