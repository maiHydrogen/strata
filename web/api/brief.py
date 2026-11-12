"""POST /api/brief: one turn of the STRATA brief chatbot (Vercel Python serverless function).

Stateless: the browser keeps the session state and sends it back with each message.
Request  {"state": {...} | null, "message": "..." | null}
Response {"reply", "state", "panel", "summarised", "done", "brief" (only once confirmed)}

The Gemini key comes from the GEMINI_API_KEY environment variable (set it in the Vercel
project settings, never in the code). If STRATA_ACCESS_CODE is set, requests must send it
in the X-Strata-Code header, so strangers cannot spend the free-tier quota.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib"))

from src.brief.chat import QUESTIONS, BriefSession  # noqa: E402
from src.brief.llm import GeminiClient, LLMError  # noqa: E402
from src.brief.schema import MUST  # noqa: E402

MAX_MESSAGE = 2000  # characters
MAX_BODY = 64_000  # bytes
LABELS = {"building_use": "Use", "floors_now": "Floors", "family": "Household", "programme": "Rooms",
          "site.plot": "Plot", "site.north": "Front faces", "site.roads": "Roads", "site.city": "City"}
USE = {"single_family": "Home for one family", "home_with_shop": "Home with a shop", "rental_floors": "Floors to rent"}
NORTH = {"N": "North", "NE": "North east", "E": "East", "SE": "South east", "S": "South", "SW": "South west",
         "W": "West", "NW": "North west"}


def _text(path, b):
    """Plain-language value of one Must item (no dashes, metres with feet in brackets)."""
    if path == "building_use":
        return USE.get(b.building_use, b.building_use)
    if path == "floors_now":
        later = f", designed for {b.floors_future}" if b.floors_future and b.floors_future != b.floors_now else ""
        return f"G+{b.floors_now - 1} ({b.floors_now} storeys){later}"
    if path == "family":
        f = b.family
        extra = [f"{n} {w}" for n, w in ((f.elderly, "elderly"), (f.children, "children"), (f.disabled, "wheelchair"))
                 if n]
        return f"{f.persons} people" + (f" ({', '.join(extra)})" if extra else "")
    if path == "programme":
        p = b.programme
        extras = [n for n in ("puja", "study", "store") if getattr(p, n)]
        return (f"{p.bedrooms} bedrooms, {p.bathrooms_attached} attached and {p.bathrooms_common} common baths"
                + (f", {', '.join(extras)}" if extras else ""))
    if path == "site.plot":
        p = b.site.plot
        return f"{p.width:.2f} x {p.depth:.2f} m ({p.width / 0.3048:.0f} x {p.depth / 0.3048:.0f} ft), {p.area:.0f} m2"
    if path == "site.north":
        return NORTH.get(b.site.north, "Unknown")
    if path == "site.roads":
        return ", ".join(f"{r.side} {r.width:.1f} m" for r in b.site.roads)
    if path == "site.city":
        return b.site.city
    return ""


def panel(session):
    """The live brief card: each Must item with its value and where it came from."""
    final = session.final_brief() if session.brief.site.plot else None
    items = []
    for path in MUST:
        source = session.brief.sources.get(path)
        if source == "client":
            status, value = "given", _text(path, session.brief)
        elif path in session.accepted_defaults and final is not None:
            status, value = "assumed", _text(path, final)
        elif path in session.accepted_defaults:
            status, value = "assumed", QUESTIONS[path][1]
        else:
            status, value = "missing", None
        items.append({"path": path, "label": LABELS[path], "status": status, "value": value,
                      "asking": session.asked == path})
    return {"items": items, "given": sum(i["status"] == "given" for i in items), "total": len(items)}


def turn(body, llm_factory=GeminiClient):
    state, message = body.get("state"), body.get("message")
    if message is not None and (not isinstance(message, str) or not message.strip()):
        return 400, {"error": "empty message"}
    if message and len(message) > MAX_MESSAGE:
        return 400, {"error": f"message longer than {MAX_MESSAGE} characters"}
    if state is None and message is None:
        s = BriefSession(None)
        return 200, {"reply": s.start(), "state": s.to_state(), "panel": panel(s), "summarised": False, "done": False}
    try:
        llm = llm_factory()
    except LLMError as e:
        return 503, {"error": str(e)}
    s = BriefSession.from_state(llm, state)
    reply = s.reply(message or "")
    out = {"reply": reply, "state": s.to_state(), "panel": panel(s), "summarised": s.summarised, "done": s.done}
    if s.done:
        out["brief"] = s.final_brief().model_dump(mode="json")
    return 200, out


class handler(BaseHTTPRequestHandler):  # noqa: N801 (Vercel looks for this name)
    def do_POST(self):  # noqa: N802
        code = os.environ.get("STRATA_ACCESS_CODE")
        if code and self.headers.get("X-Strata-Code") != code:
            return self._send(401, {"error": "access code required"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._send(413, {"error": "request too large"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "invalid JSON"})
        try:
            status, out = turn(body)
        except Exception as e:  # a broken state from the browser, never a stack trace to the client
            status, out = 400, {"error": f"could not continue the conversation ({type(e).__name__})"}
        self._send(status, out)

    def _send(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
