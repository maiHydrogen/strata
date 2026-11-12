"""Brief -> spec: every code value the design must respect, each with its citation.

Deterministic: the brief (with matrix defaults applied) becomes the lookup context
for the knowledge graph, and every value in the spec carries the clause it came
from, its status and any assumption behind it. Values the graph cannot settle are
listed under `unresolved` (with what they need) or `deferred` (decided at layout,
e.g. parking, which depends on carpet area). Engineering defaults (matrix section E)
are kept apart from code values.
"""

import math

from src.brief.schema import Brief
from src.inference.kg import KnowledgeGraph

SCHEMA_VERSION = "0.1"
FLOOR_HEIGHT = 3.0  # m, matrix E1 (practice; NBC minimum room height checked in the spec)

# Matrix section E: practice defaults the client or engineer may override
ENGINEERING_DEFAULTS = [
    ("floor_to_floor_height", FLOOR_HEIGHT, "m", "E1"),
    ("structural_system", "RCC frame with brick infill", None, "E2"),
    ("foundation", "isolated footings, 1.5 m deep", None, "E4"),
    ("external_wall", 0.23, "m brick + 12-15 mm plaster", "E5"),
    ("internal_wall", 0.115, "m brick + 12-15 mm plaster", "E5"),
    ("main_door", "1.0 x 2.1", "m", "E6"),
    ("room_door", "0.9 x 2.1", "m", "E6"),
    ("bath_door", "0.75 x 2.1", "m", "E6"),
    ("sill_level", 0.9, "m", "E7"),
    ("lintel_level", 2.1, "m", "E7"),
    ("roof", "flat RCC roof with waterproofing", None, "E9"),
]

OCCUPANCY = {"single_family": "A-2", "rental_floors": "A-2"}  # NBC 2016 Part 4 cl. 3.1.2(b)


def _entry(value, inputs=None):
    e = {"element": value.element, "quantity": value.quantity, "comparator": value.comparator,
         "value": value.value if value.text is None else value.text, "unit": value.unit,
         "source": {"standard": value.standard, "clause": value.clause, "page": value.page,
                    "jurisdiction": value.jurisdiction, "status": value.status}}
    if value.note:
        e["note"] = value.note
    if value.assumptions:
        e["assumptions"] = list(value.assumptions)
    if inputs:
        e["inputs"] = inputs
    return e


class _Spec:
    def __init__(self, kg, jurisdiction):
        self.kg, self.jurisdiction = kg, jurisdiction
        self.unresolved = []
        # (element, quantity) pairs some rule covers here; asking for others is not a gap
        self.covered = {(r["elementKey"], r["quantityKey"]) for r in kg.query("requirements", siteKey=jurisdiction)}

    def get(self, quantity, element, comparator=None, inputs=None, **context):
        if (element, quantity) not in self.covered:
            return None
        try:
            v = self.kg.lookup(quantity, element, self.jurisdiction, comparator=comparator, **context)
        except LookupError as e:
            self.unresolved.append({"element": element, "quantity": quantity, "reason": str(e)})
            return None
        return _entry(v, inputs)

    def all(self, quantities, element, **context):
        return [e for q in quantities if (e := self.get(q, element, **context))]


def _rooms(brief):
    """(name, element type, lookup context) for every room in the programme.

    NBC 2016 Part 3 cl. 12.2.2: one habitable room of a dwelling is at least 9.5 m2 and the
    others at least 7.5 m2. The living room is taken as that principal room.
    """
    p = brief.programme
    rooms = [(f"Living room {i + 1}" if p.living > 1 else "Living room", "habitable_room",
              {"room_role": "principal" if i == 0 else "other"}) for i in range(p.living)]
    rooms += [(f"Bedroom {i + 1}", "habitable_room", {"room_role": "other"}) for i in range(p.bedrooms)]
    if p.dining and p.kitchen != "with_dining":
        rooms.append(("Dining", "habitable_room", {"room_role": "other"}))
    if p.study:
        rooms.append(("Study", "habitable_room", {"room_role": "other"}))
    rooms.append(("Kitchen", "kitchen", {"kitchen_type": p.kitchen}))
    rooms += [(f"Attached bathroom {i + 1}", "bath_wc", {}) for i in range(p.bathrooms_attached)]
    rooms += [(f"Common bathroom {i + 1}", "bath_wc", {}) for i in range(p.bathrooms_common)]
    if p.store:
        rooms.append(("Store", "store", {}))
    return rooms


def _envelope(site, plot_entries):
    """Plot minus setbacks: a road side keeps its front setback (bye-law 27(c)), others side/rear."""
    front = {e["inputs"]["road"]: e["value"] for e in plot_entries if e["quantity"] == "front_setback"}
    other = {e["quantity"]: e["value"] for e in plot_entries if e["quantity"] in ("side_setback", "rear_setback")}
    if len(other) < 2 or not front:
        return None
    gap = {side: front.get(side, other["rear_setback" if side == "rear" else "side_setback"])
           for side in ("front", "rear", "left", "right")}
    return {"width": round(site.plot.width - gap["left"] - gap["right"], 3),
            "depth": round(site.plot.depth - gap["front"] - gap["rear"], 3),
            "setbacks": gap, "unit": "m"}


def build_spec(brief: Brief, kg: KnowledgeGraph | None = None) -> dict:
    """The design spec for a brief. Defaults are applied first and their sources kept."""
    kg = kg or KnowledgeGraph()
    brief = brief.with_defaults()
    site = brief.site
    jurisdiction = (site.city or "").strip().lower()
    known = {r["jurKey"] for r in kg.query("jurisdictions", siteKey=jurisdiction)}
    notes = []
    if jurisdiction not in known:
        notes.append(f"No bye-law layer for '{site.city}'; national (NBC 2016) values only.")
        jurisdiction = "india"
    s = _Spec(kg, jurisdiction)

    # ---- context derived from the brief, each with how it was obtained ----
    occupancy = OCCUPANCY.get(brief.building_use)
    if occupancy is None:
        notes.append(f"Building use '{brief.building_use}' is not a one or two family dwelling (A-2); "
                     "staircase rules for its occupancy are not encoded.")
    plinth = s.get("height", "plinth")
    plinth_m = plinth["value"] if plinth else 0.45
    storeys = max(brief.floors_now, brief.floors_future)
    height = round(storeys * FLOOR_HEIGHT + plinth_m, 3)
    road = site.main_road
    context = {
        "occupancy": {"value": occupancy, "from": f"building use '{brief.building_use}' (NBC 2016 Part 4 cl. 3.1.2(b))"},
        "building_height": {"value": height, "from": f"{storeys} storeys (floors_future, designed for) x "
                                                     f"{FLOOR_HEIGHT} m (E1) + plinth {plinth_m} m"},
        "road_width": {"value": road.width if road else None,
                       "from": f"widest abutting road, {brief.sources.get('site.roads')}"},
        "plot_area": {"value": site.plot.area if site.plot else None, "from": brief.sources.get("site.plot")},
        "in_ulb": {"value": "yes" if site.in_ulb else "no", "from": brief.sources.get("site.in_ulb")},
    }
    ctx = {k: v["value"] for k, v in context.items() if v["value"] is not None}

    # ---- rooms ----
    rooms = []
    for name, element, extra in _rooms(brief):
        reqs = s.all(("area", "width", "height"), element, **extra)
        # cl. 20.1.2 openings for habitable rooms and kitchens; bathrooms have their own ventilator rule
        if element in ("habitable_room", "kitchen"):
            reqs += s.all(("opening_ratio",), "kitchen" if element == "kitchen" else "room")
        if element in ("bathroom", "wc", "bath_wc"):  # cl. 12.4.3-12.4.4
            reqs += s.all(("external_wall", "ventilator_area", "ventilator_side", "opens_to_kitchen"), element)
        rooms.append({"name": name, "type": element, **extra, "requirements": reqs})

    # ---- building parts ----
    building = [e for e in [
        plinth,
        s.get("height", "parapet", comparator="ge"),
        s.get("height", "parapet", comparator="le"),
        *(s.all(("width", "tread", "riser", "headroom"), "staircase", occupancy=occupancy) if occupancy else []),
        s.get("risers_per_flight", "staircase"),
        s.get("headroom", "habitable_room"),
        *s.all(("width", "height"), "exit_door"),
        s.get("width", "corridor"),
    ] if e]
    materials = s.all(("grade",), "rc_concrete") + s.all(("elongation", "proof_stress"), "reinforcement")

    # ---- plot and planning (bye-laws) ----
    plot, checks, deferred = [], [], []
    if site.plot:
        for q in ("area", "width"):
            e = s.get(q, "plot", in_ulb=ctx["in_ulb"])
            if e:
                plot.append(e)
                actual = site.plot.area if q == "area" else site.plot.width
                checks.append({"check": f"plot {q} >= {e['value']} {e['unit']}", "actual": round(actual, 2),
                               "pass": actual >= e["value"] - 1e-9, "source": e["source"]})
        for r in site.roads:
            e = s.get("front_setback", "plot", inputs={"road": r.side, "road_width": r.width,
                                                      "building_height": height},
                      road_width=r.width, building_height=height)
            if e:
                plot.append(e)
        plot += s.all(("side_setback", "rear_setback"), "plot", building_height=height)
        envelope = _envelope(site, plot)
        if envelope:
            checks.append({"check": "buildable envelope after setbacks", "actual": envelope,
                           "pass": envelope["width"] > 0 and envelope["depth"] > 0})
        if road:
            plot += s.all(("far",), "plot", road_width=road.width)
            plot += s.all(("far_max",), "plot", road_width=road.width, plot_area=site.plot.area)
        pits = s.get("percolation_pits", "plot", building_type="house")
        if pits:
            pits["required_count"] = math.ceil(site.plot.area * pits["value"] - 1e-9)
            plot.append(pits)
        plot += s.all(("rainwater_storage",), "plot", building_type="house", plot_area=site.plot.area)
    parking_rules = s.kg.query("requirements", siteKey=jurisdiction, elementKey="dwelling_unit")
    if parking_rules:
        deferred.append({"element": "dwelling_unit", "quantity": "parking",
                         "needs": ["carpet_area"], "reason": "depends on the carpet area fixed by the layout",
                         "clauses": sorted({f"{r['standard']}, {r['clauseId']}, p. {r['page']}" for r in parking_rules})})

    # ---- structure and site facts ----
    structure = s.all(("imposed_load",), "floor", room_use="rooms")
    structure += s.all(("imposed_load",), "floor", room_use="toilets")
    structure += s.all(("imposed_load",), "floor", room_use="circulation")
    structure += s.all(("imposed_load",), "floor", room_use="balcony")
    structure += s.all(("imposed_load",), "roof", roof_access="access")
    facts = [_entry(v) for v in kg.site_facts(jurisdiction).values()]
    services = s.all(("water_demand",), "building")  # needs the city population, not in the graph yet

    return {
        "schema_version": SCHEMA_VERSION,
        "jurisdiction": jurisdiction,
        "brief_sources": brief.sources,
        "context": context,
        "rooms": rooms,
        "building": building,
        "materials": materials,
        "plot": plot,
        "checks": checks,
        "structure": structure,
        "site": facts,
        "services": services,
        "defaults": [{"item": name, "value": v, "unit": u, "source": f"engineering default ({item})"}
                     for name, v, u, item in ENGINEERING_DEFAULTS],
        "deferred": deferred,
        "unresolved": s.unresolved,
        "notes": notes,
    }
