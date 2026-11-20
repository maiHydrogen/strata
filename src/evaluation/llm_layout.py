"""Arm B of the Phase 9 ablation: the LLM draws the whole layout from the brief and the knowledge graph.

The LLM gets what STRATA's later stages get: the brief, the rooms the programme asks for and every
requirement the knowledge graph inferred for them (minimum areas and widths, opening ratios, stair,
door and corridor sizes, the buildable envelope after the bylaw setbacks), each with its clause.
It returns a complete layout as schema-checked JSON. There is no GA, no rule-checker feedback and no
repair: an invalid reply is retried with the parse error only, as for STRATA's own proposals.
Arm B therefore measures what the knowledge graph alone adds to an LLM; arm C adds the search and
the independent checks.
"""

import json
import time

from pydantic import BaseModel, Field, ValidationError

from src.brief.llm import LLMError
from src.spatial.layout import Column, Door, Floor, Layout, Materials, Room, Stair, Window, derive_walls
from src.spatial.proposals import first_json_object

RETRIES = 3
MAX_TOKENS = 12_288
TYPES = ("living", "bedroom", "dining", "study", "kitchen", "bath_wc", "store", "puja", "stair", "corridor",
         "balcony", "utility", "parking")

SYSTEM = """You are an architect drawing a complete, buildable, code-compliant house plan for a plot in India.
Coordinates are metres in the plot frame: origin at the plot's front-left corner seen from the road, x along the
road frontage, y into the plot. Every room is a polygon (list of [x, y] corners, counter-clockwise, no repeated
first point). On each floor the rooms must tile the floor plate without overlaps: neighbouring rooms share
edges, which are the wall centrelines; the outer edges are the outer face of the building. Keep the whole
footprint inside the buildable envelope given. Each upper floor has a stair room in the same place as the
one below it. Doors sit on the shared edge of the two rooms they join ("outside" for the main entrance on the
ground floor; exactly one main door). Windows sit on an external edge of their room. Columns stand at room
corners on a regular grid, the same points on every floor. Meet every requirement listed, with its clause
in mind. Return only JSON matching the schema."""


class BRoom(BaseModel):
    id: str
    type: str = Field(description="one of: " + ", ".join(TYPES))
    name: str = ""
    polygon: list[list[float]]


class BDoor(BaseModel):
    between: list[str] = Field(description="two room ids, or a room id and 'outside'")
    at: list[float]
    width: float
    main: bool = False


class BWindow(BaseModel):
    room: str
    at: list[float]
    width: float
    height: float = 1.2
    kind: str = Field("window", description="window, or ventilator for a bath")


class BStair(BaseModel):
    flight_width: float
    riser: float
    tread: float
    risers_per_flight: int


class BFloor(BaseModel):
    level: int
    rooms: list[BRoom]
    doors: list[BDoor]
    windows: list[BWindow]
    columns: list[list[float]]


class BLayout(BaseModel):
    stair: BStair
    floors: list[BFloor]


def _req(q, role=None):
    src = q.get("source") or {}
    cite = f"{src.get('standard', '').split('(')[0].split(',')[0].strip()} {src.get('clause', '')}".strip()
    element = q["element"] + {"principal": " (largest)", "other": " (other)"}.get(role, "")
    return f"{element} {q['quantity']} {'>=' if q['comparator'] == 'ge' else '<=' if q['comparator'] == 'le' else '='} " \
           f"{q['value']} {q.get('unit') or ''} ({cite})"


def prompt(brief, spec):
    env = next((c["actual"] for c in spec.get("checks", []) if c["check"].startswith("buildable envelope")), None)
    p = brief.programme
    lines = [f"Plot: {brief.site.plot.width:.2f} m wide (along the road) x {brief.site.plot.depth:.2f} m deep, "
             f"city {brief.site.city}, use {brief.building_use}, {brief.floors_now} storeys now "
             f"(structure for {brief.floors_future}). Family of {brief.family.persons}."]
    if env:
        sb = env["setbacks"]
        lines.append(f"Buildable envelope after setbacks: x from {sb['left']} to {sb['left'] + env['width']:.3f}, "
                     f"y from {sb['front']} to {sb['front'] + env['depth']:.3f} (setbacks {sb}).")
    lines.append(f"Programme: {p.bedrooms} bedrooms, {p.bathrooms_attached} attached and {p.bathrooms_common} "
                 f"common baths, kitchen ({p.kitchen}), dining {p.dining}, puja {p.puja}, study {p.study}, "
                 f"store {p.store}.")
    lines.append("Rooms the programme needs (place them on the floors you choose):")
    lines += [f"- {r['name']} ({r['type']})" for r in spec.get("rooms", [])]
    lines.append("Requirements from the knowledge graph (NBC 2016, bylaws, IS codes):")
    seen = set()
    reqs = [(q, r.get("room_role")) for r in spec.get("rooms", []) for q in r["requirements"]]
    for q, role in reqs + [(q, None) for q in spec.get("building", [])]:
        key = (q["element"], role, q["quantity"], q["comparator"], str(q["value"]))
        if key not in seen and isinstance(q["value"], (int, float)):
            seen.add(key)
            lines.append("- " + _req(q, role))
    defaults = [f"{d['item']} {d['value']} {d.get('unit') or ''}" for d in spec.get("defaults", [])]
    lines.append("Engineering defaults: " + "; ".join(defaults))
    return "\n".join(lines)


def to_layout(data: BLayout, name, brief, floor_to_floor=3.0):
    st = data.stair
    stair = Stair(flight_width=st.flight_width, riser=st.riser, tread=st.tread, risers_per_flight=st.risers_per_flight)
    floors = []
    for f in sorted(data.floors, key=lambda f: f.level):
        rooms = []
        for r in f.rooms:
            pts = [(float(x), float(y)) for x, y, *_ in r.polygon]
            if len(pts) > 1 and pts[0] == pts[-1]:
                pts = pts[:-1]
            while len(pts) < 4 and pts:
                pts.append(pts[-1])
            t = r.type if r.type in TYPES else "utility"
            rooms.append(Room(id=r.id, type=t, name=r.name, polygon=pts, stair=stair if t == "stair" else None))
        doors = [Door(id=f"D{i + 1}", between=(d.between[0], d.between[1] if len(d.between) > 1 else "outside"),
                      at=(d.at[0], d.at[1]), width=d.width, main=d.main) for i, d in enumerate(f.doors)]
        wins = [Window(id=f"W{i + 1}", room=w.room, at=(w.at[0], w.at[1]), width=w.width, height=w.height,
                       sill=1.8 if w.kind == "ventilator" else 0.9,
                       kind="ventilator" if w.kind == "ventilator" else "window") for i, w in enumerate(f.windows)]
        cols = [Column(id=f"C{i + 1}", at=(c[0], c[1])) for i, c in enumerate(f.columns) if len(c) >= 2]
        fl = Floor(level=len(floors), name=f"Level {len(floors)}", floor_to_floor=floor_to_floor, rooms=rooms,
                   doors=doors, windows=wins, columns=cols)
        fl.walls = derive_walls(fl)
        floors.append(fl)
    return Layout(name=name, source={"kind": "LLM + knowledge graph (arm B)"},
                  plot={"width": round(brief.site.plot.width, 4), "depth": round(brief.site.plot.depth, 4)},
                  floors=floors, plinth_height=0.45, parapet_height=1.0,
                  materials=Materials(concrete_grade=25, steel_proof_stress=500, steel_elongation=14.5,
                                      masonry="burnt clay brick"))


def draw(llm, brief, spec, name):
    """(Layout or None, log). log records each call; an error ends the attempt."""
    text = prompt(brief, spec)
    log, error = [], None
    for _ in range(RETRIES):
        full = text if error is None else f"{text}\n\nYour previous JSON was invalid: {error}\nReturn corrected JSON."
        t0 = time.time()
        try:
            raw = llm.complete_json(SYSTEM, full, BLayout, max_tokens=getattr(llm, "long_output", MAX_TOKENS))
        except LLMError as e:
            log.append({"error": str(e)})
            return None, log
        log.append({"prompt": full, "response": raw, "seconds": round(time.time() - t0, 1)})
        try:
            return to_layout(BLayout.model_validate(first_json_object(raw)), name, brief), log
        except (ValidationError, json.JSONDecodeError, ValueError, IndexError, TypeError) as e:
            error = str(e).splitlines()[0][:300]
            log[-1]["invalid"] = error
    return None, log
