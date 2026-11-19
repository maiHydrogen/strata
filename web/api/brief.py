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
from src.brief.llm import LLMError, make_client  # noqa: E402
from src.brief.schema import MUST  # noqa: E402

MAX_MESSAGE = 2000  # characters
MAX_BODY = 64_000  # bytes
LABELS = {"building_use": "Use", "floors_now": "Floors", "family": "Household", "programme": "Rooms",
          "site.plot": "Plot", "site.north": "Front faces", "site.roads": "Roads", "site.city": "City",
          "finishes": "Finishes", "electrical": "Electrical points"}
# The rest of the information requirements matrix (sections A and B): defaults until the client says
# otherwise. (path, label, matrix item)
OPTIONAL = [
    ("parking", "Parking", "A6"), ("balcony_per_bedroom_floor", "Balconies", "A7"), ("lift", "Lift", "A7"),
    ("budget", "Budget", "A8"), ("vastu", "Vastu", "A9"), ("kitchen", "Kitchen", "A10"),
    ("finishes", "Finishes", "A11"), ("electrical", "Electrical points", "A12"),
    ("water_source", "Water source", "A13"), ("rental_unit", "Rental unit", "A14"),
    ("site.plot_level_below_road", "Plot level", "B6"), ("site.soil_bearing_capacity", "Soil", "B7"),
    ("site.water_table_below_foundation", "Water table", "B8"), ("site.water_main", "Water and sewer", "B11"),
    ("site.power_phase", "Power supply", "B12"), ("site.storm_drain", "Storm drain", "B13"),
    ("site.neighbour_noc", "Common wall", "bye-law 26"),
    ("form", "Plan form", "client wish"),
]
KITCHEN = {"separate_dining": "closed kitchen, separate dining", "with_dining": "kitchen with dining",
           "with_store": "kitchen with a store"}
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
    s = b.site
    yes = lambda v, a, b_: a if v else b_  # noqa: E731
    if path == "parking" and b.parking:
        p = b.parking
        return (", ".join(x for x in (f"{p.cars} car" if p.cars else "", f"{p.two_wheelers} two-wheeler"
                                      if p.two_wheelers else "") if x) or "none") + (", covered" if p.covered else "")
    if path == "balcony_per_bedroom_floor":
        return yes(b.balcony_per_bedroom_floor, "a balcony on each bedroom floor", "no balconies")
    if path == "lift":
        return yes(b.lift, "lift", "no lift")
    if path == "budget":
        return (b.budget or "").replace("_", " ")
    if path == "vastu":
        return yes(b.vastu, "follow Vastu", "not needed")
    if path == "kitchen" and b.programme:
        return KITCHEN.get(b.programme.kitchen, b.programme.kitchen)
    if path == "water_source":
        return {"municipal": "municipal supply", "borewell": "borewell", "both": "municipal and borewell"}.get(
            b.water_source, b.water_source)
    if path == "rental_unit":
        return yes(b.rental_unit, "a unit to rent out", "none")
    if path == "site.plot_level_below_road":
        v = s.plot_level_below_road
        return "level with the road" if not v else f"{v:.2f} m below the road" if v > 0 else f"{-v:.2f} m above the road"
    if path == "site.soil_bearing_capacity":
        return f"bearing capacity {s.soil_bearing_capacity:g} kN/m2" if s.soil_bearing_capacity else "not known"
    if path == "site.water_table_below_foundation":
        return yes(s.water_table_below_foundation, "below the foundation", "high, above the foundation")
    if path == "site.water_main":
        return f"{yes(s.water_main, 'water main', 'no water main')}, {yes(s.sewer, 'sewer', 'septic tank (no sewer)')}"
    if path == "site.power_phase":
        return f"{s.power_phase}-phase" if s.power_phase else "not known"
    if path == "site.storm_drain":
        return yes(s.storm_drain, "storm drain on the road", "no storm drain")
    if path == "form":
        wishes = [w for w, on in (("central courtyard with a verandah", b.courtyard), ("stair up to the terrace",
                  b.roof_stair), ("symmetrical", b.symmetric)) if on]
        return ", ".join(wishes) or "STRATA chooses"
    if path == "site.neighbour_noc":
        return yes(s.neighbour_noc, "neighbour agrees to a common wall", "no common wall")
    if path == "finishes" and b.finishes:
        f = b.finishes
        return (f"{f.floor_living} in living areas, {f.floor_bedrooms} in bedrooms, bathroom tiles to "
                f"{f.bath_dado:.1f} m, {f.paint.replace('_', ' ')} paint")
    if path == "electrical" and b.electrical:
        e = b.electrical
        parts = [f"AC in {', '.join(e.ac_rooms)}" if e.ac_rooms else "no AC",
                 f"TV in {', '.join(e.tv_rooms)}" if e.tv_rooms else "", "geysers" if e.geyser else "no geysers",
                 f"{e.extra_sockets} extra sockets per room" if e.extra_sockets else ""]
        return ", ".join(x for x in parts if x)
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
    for it in items:
        it["group"] = "needed"
    full = session.final_brief()  # defaults filled, so every item can show what would be assumed
    src = session.brief.sources
    for path, label, item in OPTIONAL:
        key = "programme" if path == "kitchen" else path
        given = src.get(key) == "client" or (path == "site.water_main" and src.get("site.sewer") == "client")
        if path == "kitchen":
            given = src.get("programme.kitchen") == "client"
        if path == "form":
            given = any(src.get(k) == "client" for k in ("courtyard", "roof_stair", "symmetric"))
        items.append({"path": path, "label": label, "item": item, "group": "also",
                      "status": "given" if given else "assumed", "value": _text(path, full) or "-",
                      "asking": session.asked == "preferences" and path in ("finishes", "electrical")})
    needed = [i for i in items if i["group"] == "needed"]
    return {"items": items, "given": sum(i["status"] == "given" for i in needed), "total": len(needed),
            "also_given": sum(i["status"] == "given" for i in items if i["group"] == "also")}


def turn(body, llm_factory=make_client):
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
