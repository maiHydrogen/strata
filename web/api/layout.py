"""POST /api/layout: layouts for a confirmed brief (Vercel Python serverless function).

Request  {"brief": {...confirmed brief JSON...}, "seed": 1}
Response {"spec_notes", "envelope", "options": [{"name", "svg", "pass", "summary", "areas", "failures",
          "rooms", "layout", "genome"}], "suggestions": [...] (only when no option fits)}

Change request on an option:
Request  {"action": "revise", "brief": {...}, "genome": [...], "request": "make bedroom 2 bigger", "seed": 1}
Response as above, plus "edits": [{"edit", "accepted", "reason"}] and per option "wishes": [{"edit", "met", "detail"}]

Runs the same code as scripts/make_layout.py: brief -> spec (knowledge graph, clause cited)
-> genetic algorithm -> independent rule check -> SVG preview. No LLM is called for a design; a change
request makes one LLM call that only turns the sentence into edits (src/spatial/revise.py).
If STRATA_ACCESS_CODE is set, requests must send it in the X-Strata-Code header.
"""

import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib"))

from src.brief.schema import Brief  # noqa: E402
from src.inference.spec import build_spec  # noqa: E402
from src.spatial.checker import check_layout  # noqa: E402
from src.spatial.ga import alternatives, evaluate, problem, suggest, to_layout  # noqa: E402
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


def plain_ga(reason, max_span=5.0):
    """A GA hard violation in plain words."""
    m = re.match(r"(left bay|right bay|bay) span ([\d.]+) m", reason)
    if m:
        side = {"left bay": "The left row of rooms", "right bay": "The right row of rooms"}.get(m.group(1), "The row of rooms")
        return (f"{side} is {m.group(2)} m deep from the passage to the outside wall; STRATA keeps beam spans to "
                f"{max_span:g} m (practice for this frame). The plot is too wide for rooms either side of one passage.")
    m = re.match(r"(.+) ([\d.]+) m2 < ([\d.]+) m2", reason)
    if m:
        return f"{m.group(1)} gets {float(m.group(2)):.1f} m2, below the {m.group(3)} m2 minimum"
    m = re.match(r"(.+) ([\d.]+) m wide < ([\d.]+) m", reason)
    if m:
        return f"{m.group(1)} is {float(m.group(2)):.2f} m wide, below the {m.group(3)} m minimum"
    return reason[0].upper() + reason[1:]


def design(body):
    brief = Brief.model_validate(body["brief"]).with_defaults()
    if not brief.site.plot:
        return 400, {"error": "the brief has no plot size"}
    seed = int(body.get("seed", 1)) % 10_000
    spec = build_spec(brief)
    if not any(c["check"].startswith("buildable envelope") for c in spec["checks"]):
        city = brief.site.city or "this place"
        return 422, {"error": f"STRATA has the setback bye-laws of Guwahati (GMDA) only, so it cannot work out the "
                              f"buildable area for a plot in {city}. Set the city to Guwahati in the brief, or add a "
                              "bye-law layer for this city.", "notes": spec.get("notes", [])}
    if body.get("action") == "revise":
        return revise_request(body, brief, spec, seed)
    genomes, p = alternatives(brief, spec, count=3, restarts=4, generations=120, seed=seed)
    out = options_out(genomes, p, spec)
    if not any(o["pass"] for o in out["options"]):
        out["suggestions"] = [{"change": s["change"], "fits": s["fits"], "brief": s["brief"].model_dump(mode="json")}
                              for s in suggest(brief, build_spec, restarts=2, generations=80, seed=seed)]
    return 200, out


def revise_request(body, brief, spec, seed):
    from src.brief.llm import LLMError, make_client
    from src.spatial.revise import check_edits, parse_request, revise

    text = str(body.get("request", "")).strip()[:500]
    genome = [float(x) for x in body.get("genome") or []]
    if not text or not genome:
        return 400, {"error": "send the option's genome and a change request"}
    p = problem(brief, spec)
    try:
        edits, _ = parse_request(make_client(), text, p, genome)
    except LLMError as e:
        return 503, {"error": f"the language model did not answer ({e}); try again in a minute"}
    ok, no = check_edits(edits, p, genome, spec)
    report = [{"edit": e.text(), "accepted": True, "reason": ""} for e in ok]
    report += [{"edit": e.text(), "accepted": False, "reason": r} for e, r in no]
    if not ok:
        return 200, {"edits": report, "options": [], "error": "nothing in the request can be applied to this plan"}
    try:
        genomes, q, wishes = revise(brief, spec, genome, ok, p, count=3, seed=seed)
    except ValueError as e:
        return 400, {"error": str(e)}
    out = options_out(genomes, q, spec, prefix="Revised")
    for o, w in zip(out["options"], wishes):
        o["wishes"] = w
    out["edits"] = report
    return 200, out


def options_out(genomes, p, spec, prefix="Option"):
    options = []
    for k, g in enumerate(genomes, 1):
        hard, soft, why, _ = evaluate(g, p, detail=True)
        layout = to_layout(g, p, name=f"{prefix} {k}")
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
            "does_not_fit": [plain_ga(w, p.max_span) for w in why], "rooms": rooms, "layout": json.loads(layout.model_dump_json()),
            "genome": [round(x, 6) for x in g],
        })
    env = next((c["actual"] for c in spec["checks"] if c["check"].startswith("buildable envelope")), None)
    return {"envelope": env, "options": options, "notes": spec.get("notes", [])}


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
