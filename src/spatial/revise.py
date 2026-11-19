"""Free-text change requests on an approved layout ("put the kitchen upstairs, make bedroom 2 bigger").

The LLM only translates the sentence into a fixed list of edits over the layout's own room names
(data, schema-validated); it never scores or places anything. Code then:

1. rejects edits that break a rule before any search (a room below its code minimum, the living
   room off the entrance floor, a floor that does not exist), with the reason;
2. turns the rest into the GA's terms: a fixed floor for a room (hard), swapped genes for a swap,
   and soft wishes (bigger, smaller, next to, away from, terrace end) scored in ga.evaluate;
3. re-runs the GA seeded from the approved genome, so the plan changes only as much as needed;
4. reports, per edit, whether the new options meet it. The independent checker still gates them.
"""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass
from typing import Literal

from pydantic import Field, ValidationError, create_model

from src.brief.chat import first_json_object
from src.brief.llm import LLMError
from src.spatial.ga import HEAD, alternatives, clear, decode, placed
from src.spatial.layout import Door

MAX_EDITS = 4  # fixed slots: open-ended lists make small models loop under constrained decoding
RETRIES = 2
WISH = 4.0  # soft weight of one unmet wish (an unmet adjacency preference costs 1.0)
GROW = 1.2  # "bigger" with no figure: 20% more clear area than the approved plan
TOUCH = 0.35  # m: rooms whose centreline rectangles come this close share a wall (no passage between)
OPS = ("none", "move_to_floor", "bigger", "smaller", "adjacent", "apart", "swap", "terrace", "door", "unsupported")
SERVICE = ("bath_wc", "store", "utility")  # rooms that may open off any neighbour (an attached bath, a store)
NO_ENTRY = ("bath_wc", "store", "utility", "stair")  # rooms another room may not open into
DOOR_W = {"bath_wc": 0.75, "store": 0.75}  # else 0.9, as the GA's doors
DOOR_JAMBS = 0.3  # m of wall beside a door, both sides together

SYSTEM = (
    "You turn a home owner's change request about their floor plan into a list of edits. Use only the room "
    "names given, exactly as written. Floors: 0 is the ground floor. Edit kinds: move_to_floor (room, floor); "
    "bigger or smaller (room, area_m2 if the client gives a figure, else 0); adjacent or apart (room, other); "
    "swap (room, other: exchange their places); terrace (where: front or rear: which end of the upper floor is "
    "left open as a terrace); door (room, other: room's door is moved so that it opens into other, e.g. "
    "\"the bath next to bedroom 4 should open into bedroom 4\" is room = that bath, other = Bedroom 4; use the "
    "neighbours listed to find which room the client means); unsupported (note: the part of the request no edit can express, in the client's "
    "words). Fill unused slots with kind none. Do not invent wishes the client did not state."
)


@dataclass
class Edit:
    op: str
    room: str = ""
    other: str = ""
    floor: int = 0
    area_m2: float = 0.0
    where: str = ""
    note: str = ""
    target: tuple = ()  # swap: ((floor, band, side) the room should take, same for the other), from the approved plan

    def text(self) -> str:
        return {
            "move_to_floor": f"{self.room} on floor {self.floor}",
            "bigger": f"{self.room} bigger" + (f" (at least {self.area_m2:g} m2)" if self.area_m2 else ""),
            "smaller": f"{self.room} smaller" + (f" (at most {self.area_m2:g} m2)" if self.area_m2 else ""),
            "adjacent": f"{self.room} next to {self.other}",
            "apart": f"{self.room} away from {self.other}",
            "swap": f"swap {self.room} and {self.other}",
            "terrace": f"upper-floor terrace at the {self.where}",
            "door": f"{self.room} opens into {self.other}",
            "unsupported": f"not possible as an edit: {self.note}",
        }.get(self.op, self.op)


def room_names(p) -> list[str]:
    living = [u.name for u in p.units if u.type == "living"]
    return living + [u.name for u in placed(p)]


def edit_schema(p):
    names = tuple(room_names(p)) + ("",)
    slot = create_model(
        "Edit", kind=(Literal[OPS], ...), room=(Literal[names], ...), other=(Literal[names], ...),
        floor=(int, Field(..., ge=0, le=max(0, p.floors - 1))), area_m2=(float, Field(..., ge=0)),
        where=(Literal["front", "rear", ""], ...), note=(str, ...))
    return create_model("ChangeRequest", **{f"edit{k}": (slot, ...) for k in range(1, MAX_EDITS + 1)})


def neighbours(plan) -> list[str]:
    """Per room, the rooms it shares a wall with and its floor: lets the model resolve "the bath next to bedroom 4"."""
    out = []
    for f, cells in enumerate(plan.floors):
        rooms = [r for c in cells for r in c.rooms]
        for r in rooms:
            near = [o[0] for o in rooms if o is not r and _shared(r, o)]
            if near:
                out.append(f"{r[0]} (floor {f}): next to {', '.join(near)}")
    return out


def prompt(text: str, p, plan=None) -> str:
    lines = [f"The house has {p.floors} floor{'s' if p.floors > 1 else ''} (0 = ground).",
             "Rooms: " + ", ".join(room_names(p)) + "."]
    if plan is not None:
        lines += ["Which rooms share a wall (\"Bath of X\" is X's attached bath, not a name you can use):"]
        lines += neighbours(plan)
    lines.append(f"Client's request: {text.strip()}")
    return "\n".join(lines)


def parse_request(llm, text: str, p, genome=None) -> tuple[list[Edit], list[dict]]:
    """The request as edits (kind none dropped), and the log of LLM calls. Raises LLMError if no valid reply.
    With the approved `genome`, the prompt also says which rooms are next to which."""
    schema = edit_schema(p)
    base = prompt(text, p, decode(genome, p) if genome is not None else None)
    log, error = [], None
    for _ in range(RETRIES + 1):
        full = base if error is None else f"{base}\n\nYour previous JSON was invalid: {error}\nReturn corrected JSON."
        raw = llm.complete_json(SYSTEM, full, schema)
        log.append({"prompt": full, "response": raw})
        try:
            data = schema.model_validate(first_json_object(raw)).model_dump()
        except (ValidationError, json.JSONDecodeError, ValueError) as e:
            error = str(e).splitlines()[0]
            continue
        edits = []
        for k in range(1, MAX_EDITS + 1):
            e = data[f"edit{k}"]
            if e["kind"] != "none":
                edits.append(Edit(e["kind"], e["room"], e["other"], e["floor"], e["area_m2"], e["where"], e["note"]))
        return edits, log
    raise LLMError(f"no valid change request after {RETRIES + 1} tries: {error}")


# ---------------------------------------------------------------- rules before search

def _units(p):
    return {u.name: u for u in p.units}


def _room(plan, name):
    """(floor, room tuple) of a named room in a decoded plan, or (None, None)."""
    for f, cells in enumerate(plan.floors):
        for c in cells:
            for r in c.rooms:
                if r[0] == name:
                    return f, r
    return None, None


def _cell(plan, name):
    for f, cells in enumerate(plan.floors):
        for c in cells:
            if any(r[0] == name for r in c.rooms):
                return f, c.band, c.side
    return None


def _shared(a, b):
    """The wall two plan rooms (name, type, x0, y0, x1, y1) share: (axis, v, lo, hi), or None."""
    _, _, ax0, ay0, ax1, ay1 = a
    _, _, bx0, by0, bx1, by1 = b
    for v in (x for x in (ax0, ax1) if abs(x - bx0) < 1e-6 or abs(x - bx1) < 1e-6):
        lo, hi = max(ay0, by0), min(ay1, by1)
        if hi - lo > 1e-6:
            return ("x", v, lo, hi)
    for v in (y for y in (ay0, ay1) if abs(y - by0) < 1e-6 or abs(y - by1) < 1e-6):
        lo, hi = max(ax0, bx0), min(ax1, bx1)
        if hi - lo > 1e-6:
            return ("y", v, lo, hi)
    return None


def _door_fits(plan, e, unit_type):
    """Length of the wall room and other share in the plan, and the length a door there needs."""
    fa, ra = _room(plan, e.room)
    fb, rb = _room(plan, e.other)
    s = _shared(ra, rb) if ra is not None and rb is not None and fa == fb else None
    return (s[3] - s[2]) if s else 0.0, DOOR_W.get(unit_type, 0.9) + DOOR_JAMBS


def _area(plan, name):
    f, r = _room(plan, name)
    if r is None:
        return None
    w, d = clear(r, plan, f)
    return w * d


KIND = {"bedroom": "habitable_room", "dining": "habitable_room", "study": "habitable_room", "kitchen": "kitchen",
        "bath_wc": "bath_wc", "store": "store"}


def _area_source(spec, unit_type):
    """The clause behind a room type's minimum area, from the spec."""
    for r in (spec or {}).get("rooms", []):
        principal = r.get("room_role") == "principal"
        if (unit_type == "living" and principal) or (not principal and r.get("type") == KIND.get(unit_type)):
            for q in r["requirements"]:
                if q["quantity"] == "area" and q.get("source"):
                    return f"{q['source']['standard']}, {q['source']['clause']}"
    return "the spec"


def check_edits(edits: list[Edit], p, genome=None, spec=None):
    """(accepted, rejected [(edit, reason)]). `genome` is the approved plan, used for "bigger" with no figure;
    `spec` supplies the clause cited when an edit would break a minimum."""
    units, ok, no = _units(p), [], []
    plan = decode(genome, p) if genome is not None else None
    for e in edits:
        u, o = units.get(e.room), units.get(e.other)
        need = {"move_to_floor": (u,), "bigger": (u,), "smaller": (u,), "adjacent": (u, o), "apart": (u, o),
                "swap": (u, o), "door": (u, o)}.get(e.op, ())
        if any(x is None for x in need):
            no.append((e, "names a room that is not in this house"))
            continue
        if e.op == "unsupported":
            no.append((e, "STRATA cannot express this as a layout change; edit the brief instead"))
        elif e.op == "move_to_floor":
            if not 0 <= e.floor < p.floors:
                no.append((e, f"the house has floors 0 to {p.floors - 1}"))
            elif u.type == "living" and e.floor != 0:
                no.append((e, "the living room is the entrance room on the ground floor (practice)"))
            else:
                ok.append(e)
        elif e.op in ("bigger", "smaller"):
            if u.type == "living" and e.op == "bigger" and not e.area_m2:
                no.append((e, "the living room takes the whole front cell; give a figure or make another room smaller"))
                continue
            minimum = p.principal.get("area", 9.5) if u.type == "living" else p.req.get((u.type, "area"), 0)
            if e.op == "smaller" and e.area_m2 and e.area_m2 < minimum - 1e-9:
                no.append((e, f"{e.room} must have at least {minimum:g} m2 ({_area_source(spec, u.type)})"))
                continue
            if e.op == "bigger" and not e.area_m2 and plan is not None:
                now = _area(plan, e.room)
                e = Edit(**{**asdict(e), "area_m2": round(now * GROW, 1) if now else 0.0})
            if e.op == "smaller" and not e.area_m2 and plan is not None:
                now = _area(plan, e.room)
                e = Edit(**{**asdict(e), "area_m2": round(max(minimum, now / GROW), 1) if now else 0.0})
            ok.append(e)
        elif e.op == "swap":
            if "living" in (u.type, o.type):
                no.append((e, "the living room stays at the entrance; swap two other rooms"))
            elif e.room == e.other:
                no.append((e, "a room cannot swap with itself"))
            else:
                ok.append(e)
        elif e.op == "terrace":
            if getattr(p, "court", False):
                no.append((e, "a courtyard plan builds the whole ring on every floor; it has no terrace end"))
            elif p.floors < 2:
                no.append((e, "a single-storey house has no upper-floor terrace"))
            elif e.where not in ("front", "rear"):
                no.append((e, "say front or rear"))
            else:
                ok.append(e)
        elif e.op == "door":
            why = _door_refusal(e, u, o, plan, ok)
            (no.append((e, why)) if why else ok.append(e))
        else:  # adjacent, apart
            if e.room == e.other:
                no.append((e, "names the same room twice"))
            else:
                ok.append(e)
    return ok, no


def _door_refusal(e, u, o, plan, accepted):
    """Why a door move cannot be made, or "" when it can."""
    if e.room == e.other:
        return "names the same room twice"
    if u.type == "living":
        return "the living room keeps the main entrance; name the room whose door should move"
    if o.type in NO_ENTRY:
        return f"a room cannot be entered only through a {o.type.replace('_wc', '').replace('_', ' ')} (practice)"
    if u.type not in SERVICE and o.type in ("bedroom", "kitchen"):
        return f"{e.room} would be reached only through {e.other}; habitable rooms open off the living room or verandah"
    if plan is None:
        return ""
    have, need = _door_fits(plan, e, u.type)
    if have < need - 1e-6:
        return (f"{e.room} and {e.other} share {have:.2f} m of wall; a door with its frame needs {need:.2f} m"
                if have else f"{e.room} and {e.other} do not share a wall")
    if u.type == "bath_wc" and o.type == "bedroom":  # the bath stops being common: is another bedroom left without one?
        f, _ = _room(plan, e.room)
        rooms = [r for c in plan.floors[f] for r in c.rooms]
        taken = {x.room for x in accepted if x.op == "door"} | {e.room}
        owners = {r[0][len("Bath of "):] for r in rooms if r[0].startswith("Bath of ")}
        owners |= {x.other for x in accepted if x.op == "door"} | {e.other}
        common = [r for r in rooms if r[1] == "bath_wc" and not r[0].startswith("Bath of ") and r[0] not in taken]
        lonely = [r[0] for r in rooms if r[1] == "bedroom" and r[0] not in owners]
        if lonely and not common:
            return (f"{', '.join(lonely)} would have no bathroom on {['the ground', 'the first', 'the second'][f]} "
                    "floor; a bedroom without its own bath needs a common one on its floor (practice)")
    return ""


# ---------------------------------------------------------------- in the GA

def _touch(a, b):
    (fa, ra), (fb, rb) = a, b
    if ra is None or rb is None or fa != fb:
        return False
    gap_x = max(ra[2] - rb[4], rb[2] - ra[4], 0.0)
    gap_y = max(ra[3] - rb[5], rb[3] - ra[5], 0.0)
    return gap_x <= TOUCH and gap_y <= TOUCH and not (gap_x > 1e-6 and gap_y > 1e-6)


def wish_met(e: Edit, g, plan, p) -> tuple[bool, str]:
    if e.op == "move_to_floor":
        f, _ = _room(plan, e.room)
        return f == e.floor, f"on floor {f}"
    if e.op in ("bigger", "smaller"):
        a = _area(plan, e.room)
        if a is None:
            return False, "not placed"
        met = a >= e.area_m2 - 0.05 if e.op == "bigger" else a <= e.area_m2 + 0.05
        return met, f"{a:.1f} m2 clear"
    if e.op in ("adjacent", "apart"):
        t = _touch(_room(plan, e.room), _room(plan, e.other))
        return (t if e.op == "adjacent" else not t), "share a wall" if t else "do not share a wall"
    if e.op == "terrace":
        front = g[9] >= 0.5
        return (front == (e.where == "front")), f"terrace at the {'front' if front else 'rear'}"
    if e.op == "door":
        unit = _units(p).get(e.room)
        have, need = _door_fits(plan, e, unit.type if unit else "")
        return have >= need - 1e-6, (f"share {have:.2f} m of wall" if have else "do not share a wall")
    if e.op == "swap":
        a, b = _cell(plan, e.room), _cell(plan, e.other)
        return (a, b) == tuple(e.target), "places exchanged" if (a, b) == tuple(e.target) else "not exchanged"
    return False, ""


def wish_penalty(g, plan, p) -> float:
    """Soft cost of the unmet wishes, scaled by how far off a size wish is."""
    cost = 0.0
    for e in p.wishes:
        met, _ = wish_met(e, g, plan, p)
        if met:
            continue
        if e.op in ("bigger", "smaller") and e.area_m2:
            a = _area(plan, e.room) or 0.0
            cost += WISH * min(1.0, 0.25 + abs(a - e.area_m2) / e.area_m2 * 3)
        else:
            cost += WISH
    return cost


def apply_edits(p, genome, edits: list[Edit]):
    """(problem with the edits, starting genome). Floors become fixed; swaps exchange the two rooms' genes."""
    q = copy.deepcopy(p)
    g = list(genome)
    before = decode(g, p)
    units = {u.name: u for u in q.units}
    index = {u.name: i for i, u in enumerate(placed(q))}
    for e in edits:
        if e.op == "move_to_floor":
            units[e.room].floor = e.floor
            i = index.get(e.room)
            if i is not None:
                g[HEAD + 2 * i] = (e.floor + 0.5) / q.floors
        elif e.op == "swap":
            e.target = (_cell(before, e.other), _cell(before, e.room))
            i, j = index[e.room], index[e.other]
            for k in (0, 1):
                g[HEAD + 2 * i + k], g[HEAD + 2 * j + k] = g[HEAD + 2 * j + k], g[HEAD + 2 * i + k]
            ui, uj = units[e.room], units[e.other]
            if ui.floor is not None or uj.floor is not None:  # a fixed floor travels with the place
                ui.floor, uj.floor = uj.floor, ui.floor
        elif e.op == "terrace":
            g[9] = 0.75 if e.where == "front" else 0.25
    q.wishes = list(edits)
    return q, g


def revise(brief, spec, genome, edits: list[Edit], p, count=3, restarts=3, generations=100, seed=0):
    """New options for the accepted edits, seeded from the approved genome; returns (genomes, problem, report)."""
    if len(genome) != HEAD + 2 * len(placed(p)):
        raise ValueError("the approved layout does not match this brief (it changed since); generate again")
    q, start = apply_edits(p, genome, edits)
    genomes, q = alternatives(brief, spec, count=count, restarts=restarts, generations=generations, seed=seed,
                              seeds=[start] * 4, p=q)
    report = []
    for g in genomes:
        plan = decode(g, q)
        report.append([{"edit": e.text(), "met": m, "detail": d}
                       for e in edits for m, d in [wish_met(e, g, plan, q)]])
    return genomes, q, report


def move_doors(layout, edits):
    """Apply door edits to a built layout: the room loses its doors (except to rooms that open only off it, such as
    its own attached bath) and gets one door on the wall it shares with the other room. Skipped when the new plan
    no longer has that wall: the wish is then reported as not met."""
    for e in (x for x in edits if x.op == "door"):
        for fl in layout.floors:
            by = {r.name: r for r in fl.rooms}
            a, b = by.get(e.room), by.get(e.other)
            if a is None or b is None:
                continue
            edge = a.shape.boundary.intersection(b.shape.boundary)
            parts = [g for g in getattr(edge, "geoms", [edge]) if g.geom_type == "LineString"]
            if not parts:
                continue
            line = max(parts, key=lambda g: g.length)
            width = DOOR_W.get(a.type, 0.9)
            if line.length < width + DOOR_JAMBS - 1e-6:
                continue
            inner = {r.id for r in fl.rooms if r.name.startswith(f"Bath of {a.name}")}
            fl.doors = [d for d in fl.doors if d.main or a.id not in d.between
                        or any(x in inner for x in d.between)]
            mid = line.interpolate(0.5, normalized=True)
            fl.doors.append(Door(id=f"D_{a.id}", between=(a.id, b.id), at=(round(mid.x, 4), round(mid.y, 4)),
                                 width=width))
    return layout
