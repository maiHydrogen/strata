"""POST /api/layout: layouts for a confirmed brief (Vercel Python serverless function).

Request  {"brief": {...confirmed brief JSON...}, "seed": 1}
Response {"spec_notes", "envelope", "options": [{"name", "svg", "pass", "summary", "areas", "failures",
          "rooms", "layout"}], "suggestions": [...] (only when no option fits)}

Runs the same code as scripts/make_layout.py: brief -> spec (knowledge graph, clause cited)
-> genetic algorithm -> independent rule check -> SVG preview. No LLM is called here.
If STRATA_ACCESS_CODE is set, requests must send it in the X-Strata-Code header.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib"))

from src.brief.schema import Brief  # noqa: E402
from src.inference.spec import build_spec  # noqa: E402
from src.spatial.checker import check_layout  # noqa: E402
from src.spatial.ga import alternatives, evaluate, suggest, to_layout  # noqa: E402
from src.spatial.preview import render_svg  # noqa: E402

MAX_BODY = 64_000
NAMES = {"living": "Living", "dining": "Dining", "bedroom": "Bedroom", "study": "Study", "kitchen": "Kitchen",
         "bath_wc": "Bath", "store": "Store", "stair": "Stair", "corridor": "Passage", "utility": "Utility"}


def plain(r, names):
    """A checker failure in plain words (room names, not ids), with its clause."""
    src = r.get("source", {})
    cite = f" ({src.get('standard', '').split(' (')[0]}, {src.get('clause')})" if src else ""
    subject = names.get(r["subject"].split(" (")[0], r["subject"])
    subject = {"bath_wc": "Bathrooms", "habitable_room": "Living rooms and bedrooms", "kitchen": "Kitchen",
               "store": "Store", "staircase": "Stair"}.get(subject, subject)
    return f"{subject}: {r['check']}, found {r['actual']}{cite}"


def design(body):
    brief = Brief.model_validate(body["brief"]).with_defaults()
    if not brief.site.plot:
        return 400, {"error": "the brief has no plot size"}
    seed = int(body.get("seed", 1)) % 10_000
    spec = build_spec(brief)
    genomes, p = alternatives(brief, spec, count=3, restarts=4, generations=120, seed=seed)
    options = []
    for k, g in enumerate(genomes, 1):
        hard, soft, why, _ = evaluate(g, p, detail=True)
        layout = to_layout(g, p, name=f"Option {k}")
        result = check_layout(layout, spec)
        rooms, names = [], {}
        for f, floor in zip(result["areas"]["floors"], layout.floors):
            for room in floor.rooms:
                label = room.name if room.type in ("bedroom", "study") and room.name else NAMES.get(room.type, room.type)
                names[f"{f['floor']} {room.id}"] = f"{f['floor']}, {label.lower() if label != room.name else label}"
                if room.type != "corridor":
                    rooms.append({"floor": f["floor"], "name": label, "carpet": round(f["rooms"][room.id], 1)})
        options.append({
            "name": layout.name, "svg": render_svg(layout, result), "pass": result["pass"] and hard == 0,
            "summary": result["summary"], "areas": {k2: v for k2, v in result["areas"].items() if k2 != "floors"},
            "floors": [{"floor": f["floor"], "built_up": f["built_up"], "carpet": f["carpet"]}
                       for f in result["areas"]["floors"]],
            "failures": [plain(r, names) for r in result["results"]
                         if r["pass"] is False and r["basis"] != "practice"][:12],
            "does_not_fit": why, "rooms": rooms, "layout": json.loads(layout.model_dump_json()),
        })
    env = next((c["actual"] for c in spec["checks"] if c["check"].startswith("buildable envelope")), None)
    out = {"envelope": env, "options": options, "notes": spec.get("notes", [])}
    if not any(o["pass"] for o in options):
        out["suggestions"] = [{"change": s["change"], "fits": s["fits"], "brief": s["brief"].model_dump(mode="json")}
                              for s in suggest(brief, build_spec, restarts=2, generations=80, seed=seed)]
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
            status, out = design(body)
        except (KeyError, ValueError, TypeError) as e:
            status, out = 400, {"error": f"could not read the brief ({type(e).__name__})"}
        self._send(status, out)

    def _send(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
