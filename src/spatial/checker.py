"""Independent rule checker: verifies a layout against the design spec.

A separate code path from the genetic algorithm: it reads only the layout JSON and the
spec JSON (whose every value cites its clause) and re-derives everything it checks from
the geometry, so a GA bug cannot hide behind its own scoring. No LLM.

Every result says what was checked, the actual value, whether it passed and its basis:
the cited clause from the spec ("code"); "validity" for what any buildable plan must
satisfy (no overlaps, doors on shared walls, every room reachable, columns continuous);
or "practice" for engineering rules of thumb with no code value. Code and validity
failures fail the layout; practice failures are warnings.
"""

from collections import Counter, defaultdict

from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from src.spatial.layout import EXTERNAL_WALL, INTERNAL_WALL, Layout

TOL = 0.01  # m
OPEN_AIR = {"balcony", "parking"}  # unenclosed: a window or ventilator onto them opens to external air
AREA_TOL = 0.005  # m2
MAX_SPAN = 5.0  # m, column spacing on a grid line (practice for a residential RC frame; STAAD checks members)
DOOR_MIN = {"bath_wc": 0.75, "bathroom": 0.75, "wc": 0.75, "store": 0.75}  # practice; others 0.9
DOOR_DEFAULT_MIN = 0.9
ELEMENT = {"living": "habitable_room", "bedroom": "habitable_room", "dining": "habitable_room",
           "study": "habitable_room", "kitchen": "kitchen", "bath_wc": "bath_wc", "bathroom": "bathroom",
           "wc": "wc", "store": "store", "stair": "staircase", "corridor": "corridor"}


# Checks with no clause that a layout must still pass: it is not a buildable plan otherwise.
VALIDITY = ("room polygon is valid", "rooms do not overlap", "wall has length", "opening sits on a wall",
            "opening fits on wall", "openings do not overlap", "door leads to rooms", "door joins two",
            "door is on the wall", "one main entrance", "room has a door", "window belongs", "window is on an external",
            "every room reachable", "at least ", "a stair on every floor", "flights rise", "two flights", "flight plus",
            "every column stands", "stair is in the same place", "columns inside")


class _Results:
    def __init__(self):
        self.items = []

    def add(self, category, subject, check, actual, ok, source=None, note=None):
        basis = "code" if source else ("validity" if check.startswith(VALIDITY) else "practice")
        r = {"category": category, "subject": subject, "check": check,
             "actual": round(actual, 3) if isinstance(actual, float) else actual, "pass": bool(ok),
             "basis": basis}
        if source:
            r["source"] = source
        if note:
            r["note"] = note
        self.items.append(r)
        return ok

    def skip(self, category, subject, check, reason):
        self.items.append({"category": category, "subject": subject, "check": check, "pass": None,
                           "basis": "skipped", "note": reason})


def _cmp(actual, req):
    v, c = req["value"], req["comparator"]
    if c == "ge":
        return actual >= v - TOL
    if c == "le":
        return actual <= v + TOL
    return actual == v


def _label(req):
    sym = {"ge": ">=", "le": "<=", "eq": "="}[req["comparator"]]
    return f"{req['quantity']} {sym} {req['value']}" + (f" {req['unit']}" if req.get("unit") else "")


def clear_width(poly):
    """Width of the widest strip that fits everywhere: twice the largest inset that leaves the room non-empty.

    For a rectangle this is its shorter side; for an L-shaped room it is the narrower leg.
    """
    lo, hi = 0.0, max(poly.bounds[2] - poly.bounds[0], poly.bounds[3] - poly.bounds[1])
    for _ in range(40):
        mid = (lo + hi) / 2
        inner = poly.buffer(-mid / 2, join_style="mitre")
        if inner.is_empty or inner.area < 1e-6:
            hi = mid
        else:
            lo = mid
    return lo


def _requirements(spec):
    """Requirements by element (and 'principal'/'other' for habitable rooms), from the spec."""
    req = defaultdict(dict)
    for room in spec["rooms"]:
        key = room["type"] + ("/" + room["room_role"] if room.get("room_role") else "")
        for r in room["requirements"]:
            req[key][(r["quantity"], r["comparator"])] = r
    for r in spec["building"] + spec.get("materials", []):
        req[r["element"]][(r["quantity"], r["comparator"])] = r
    return req


def _programme(spec):
    counts = Counter()
    for room in spec["rooms"]:
        counts[room["type"]] += 1
    return counts


def _envelope(spec):
    for c in spec.get("checks", []):
        if c["check"].startswith("buildable envelope") and isinstance(c.get("actual"), dict):
            return c["actual"]
    return None


def check_layout(layout: Layout, spec: dict) -> dict:
    res = _Results()
    req = _requirements(spec)
    areas = {"floors": [], "plot_area": None}
    plot = layout.plot or {}
    if plot:
        areas["plot_area"] = round(plot["width"] * plot["depth"], 3)

    prev = None
    rooms_by_floor = {}
    for floor in layout.floors:
        tag = floor.name or f"L{floor.level}"
        walls = floor.walls
        rooms = {r.id: r for r in floor.rooms}
        rooms_by_floor[floor.level] = rooms
        footprint = unary_union([r.shape for r in floor.rooms])
        carpet = _carpet(floor)
        _geometry(res, floor, tag, footprint, spec, plot)
        _walls(res, floor, tag, footprint)
        _rooms(res, floor, tag, carpet, req)
        _openings(res, floor, tag, footprint, carpet, req)
        _structure(res, floor, tag, footprint, prev)
        built = footprint.area
        areas["floors"].append({"floor": tag, "built_up": round(built, 3),
                                "carpet": round(sum(carpet.values()), 3),
                                "rooms": {rid: round(a, 3) for rid, a in carpet.items()},
                                "carpet_basis": "inside walls" if walls else "to wall centrelines (no walls given)"})
        prev = floor

    _reachability(res, layout)
    _programme_check(res, layout, spec)
    _building(res, layout, req, areas)
    _materials(res, layout, req)

    code_fail = [r for r in res.items if r["pass"] is False and r["basis"] in ("code", "validity")]
    warn = [r for r in res.items if r["pass"] is False and r["basis"] == "practice"]
    return {"layout": layout.name, "pass": not code_fail,
            "summary": {"checks": sum(r["pass"] is not None for r in res.items), "failures": len(code_fail),
                        "practice_warnings": len(warn), "skipped": sum(r["pass"] is None for r in res.items)},
            "areas": areas, "results": res.items}


# ---- geometry: overlaps, envelope ----

def _geometry(res, floor, tag, footprint, spec, plot):
    rs = floor.rooms
    for r in rs:
        if not r.shape.is_valid or r.shape.area <= 0:
            res.add("geometry", f"{tag} {r.id}", "room polygon is valid", "invalid", False)
    overlaps = [(a.id, b.id, a.shape.intersection(b.shape).area) for i, a in enumerate(rs) for b in rs[i + 1:]]
    bad = [(a, b, round(x, 3)) for a, b, x in overlaps if x > AREA_TOL]
    res.add("geometry", tag, "rooms do not overlap", bad or "none", not bad)
    env = _envelope(spec)
    if env and plot:
        s = env["setbacks"]
        allowed = box(s["left"], s["front"], plot["width"] - s["right"], plot["depth"] - s["rear"])
        outside = footprint.difference(allowed.buffer(TOL, join_style="mitre")).area
        src = next((e["source"] for e in spec["plot"] if e["quantity"] == "front_setback"), None)
        res.add("setbacks", tag, f"footprint inside the buildable envelope (setbacks {s})", round(outside, 3),
                outside <= AREA_TOL, src, note="area outside the envelope, m2")
    else:
        res.skip("setbacks", tag, "footprint inside the buildable envelope", "no plot or envelope in the spec")


# ---- walls ----

def _walls(res, floor, tag, footprint):
    if floor.walls is None:
        res.skip("walls", tag, "wall thickness, coverage and hanging walls", "no walls in the layout")
        return
    outline = footprint.boundary
    edges = unary_union([r.shape.boundary for r in floor.rooms])
    for w in floor.walls:
        need = EXTERNAL_WALL if w.external else INTERNAL_WALL
        res.add("walls", f"{tag} {w.id}", f"{'external' if w.external else 'internal'} wall thickness >= {need} m",
                w.thickness, w.thickness >= need - 1e-9, note="one brick outside, half brick inside")
        if w.line.length < TOL:
            res.add("walls", f"{tag} {w.id}", "wall has length", w.line.length, False)
        on_edge = w.line.difference(edges.buffer(w.thickness / 2 + TOL)).length
        res.add("walls", f"{tag} {w.id}", "wall lies on a room edge (not hanging)", round(on_edge, 3), on_edge <= TOL,
                note="length off any room edge, m")
    lines = [w.line.buffer(w.thickness / 2 + TOL, cap_style="flat") for w in floor.walls]
    walled = unary_union(lines) if lines else Polygon()
    for r in floor.rooms:
        bare = r.shape.boundary.difference(walled).length
        res.add("walls", f"{tag} {r.id}", "every room edge has a wall", round(bare, 3), bare <= 0.05,
                note="room edge length with no wall, m")
    # openings fit their walls without overlapping each other
    per_wall = defaultdict(list)
    for o in list(floor.doors) + list(floor.windows):
        w = _host_wall(floor.walls, o.at)
        if w is None:
            res.add("walls", f"{tag} {o.id}", "opening sits on a wall", "no wall", False)
            continue
        t = w.line.project(Point(o.at))
        per_wall[w.id].append((t - o.width / 2, t + o.width / 2, o.id, w))
    for wid, spans in per_wall.items():
        spans.sort()
        length = spans[0][3].line.length
        for a0, a1, oid, _ in spans:
            res.add("walls", f"{tag} {oid}", f"opening fits on wall {wid}", f"{a0:.2f}..{a1:.2f} of {length:.2f}",
                    a0 >= -TOL and a1 <= length + TOL)
        for (a0, a1, x, _), (b0, b1, y, _) in zip(spans, spans[1:]):
            res.add("walls", f"{tag} {x}/{y}", f"openings do not overlap on wall {wid}", round(a1 - b0, 3),
                    b0 >= a1 - TOL)


def _host_wall(walls, at):
    p = Point(at)
    best = min(walls, key=lambda w: w.line.distance(p), default=None)
    return best if best is not None and best.line.distance(p) <= best.thickness / 2 + TOL else None


def _carpet(floor):
    """Room area inside the walls (carpet area); to wall centrelines when the layout has no walls."""
    if not floor.walls:
        return {r.id: r.shape.area for r in floor.rooms}
    walls = unary_union([w.line.buffer(w.thickness / 2, cap_style="flat", join_style="mitre") for w in floor.walls])
    return {r.id: r.shape.difference(walls).area for r in floor.rooms}


# ---- rooms: area, width, height ----

def _rooms(res, floor, tag, carpet, req):
    habitable = [r for r in floor.rooms if ELEMENT.get(r.type) == "habitable_room"]
    for r in floor.rooms:
        el = ELEMENT.get(r.type)
        if el is None:
            continue
        key = el
        if el == "habitable_room":
            key = "habitable_room/other"
        reqs = req.get(key, {}) if el not in ("staircase", "corridor") else {}
        shape = r.shape
        if floor.walls:
            inner = shape.difference(unary_union([w.line.buffer(w.thickness / 2, cap_style="flat")
                                                  for w in floor.walls]))
            inner = max(inner.geoms, key=lambda g: g.area) if hasattr(inner, "geoms") else inner
        else:
            inner = shape
        width = clear_width(inner)
        subject = f"{tag} {r.id} ({r.type})"
        for (q, _), rq in reqs.items():
            if q == "area":
                res.add("rooms", subject, _label(rq), carpet[r.id], _cmp(carpet[r.id], rq), rq["source"])
            elif q == "width":
                res.add("rooms", subject, _label(rq) + " (clear)", width, _cmp(width, rq), rq["source"])
            elif q == "height":
                h = floor.clear_height(r)
                res.add("ceiling", subject, _label(rq), h, _cmp(h, rq), rq["source"])
        if el == "corridor":
            rq = req.get("corridor", {}).get(("width", "ge"))
            if rq:
                res.add("rooms", subject, _label(rq) + " (clear)", width, _cmp(width, rq), rq["source"])
        if el == "staircase":
            _stair(res, floor, r, subject, inner, req.get("staircase", {}))
    # NBC 12.2.2: at least one habitable room of the dwelling meets the principal-room size
    principal = req.get("habitable_room/principal", {})
    if habitable and principal and floor.level == 0:
        big = max(habitable, key=lambda r: carpet[r.id])
        for (q, _), rq in principal.items():
            if q == "area":
                res.add("rooms", f"{tag} {big.id} (largest habitable room)", "principal room " + _label(rq),
                        carpet[big.id], _cmp(carpet[big.id], rq), rq["source"])
            elif q == "width":
                w = clear_width(big.shape if not floor.walls else big.shape.difference(unary_union(
                    [w.line.buffer(w.thickness / 2, cap_style="flat") for w in floor.walls])))
                res.add("rooms", f"{tag} {big.id} (largest habitable room)", "principal room " + _label(rq) + " (clear)",
                        w, _cmp(w, rq), rq["source"])


def _stair(res, floor, room, subject, inner, reqs):
    s = room.stair
    if s is None:
        res.skip("stairs", subject, "stair flight width, riser, tread", "no stair details in the layout")
        return
    for (q, _), rq in reqs.items():
        actual = {"width": s.flight_width, "tread": s.tread, "riser": s.riser,
                  "risers_per_flight": s.risers_per_flight, "headroom": s.headroom}.get(q)
        if actual is None:
            res.skip("stairs", subject, _label(rq), f"{q} not given")
            continue
        res.add("stairs", subject, _label(rq), float(actual), _cmp(actual, rq), rq["source"])
    rise = s.riser * s.risers_per_flight * s.flights
    res.add("stairs", subject, f"flights rise one storey ({floor.floor_to_floor} m)", rise,
            abs(rise - floor.floor_to_floor) <= 0.02)
    minx, miny, maxx, maxy = inner.bounds
    short, long_ = sorted((maxx - minx, maxy - miny))
    going = (s.risers_per_flight - 1) * s.tread + s.flight_width  # one flight plus the mid landing
    res.add("stairs", subject, "two flights side by side fit the stair room", f"{2 * s.flight_width:.2f} in {short:.2f}",
            2 * s.flight_width <= short + TOL)
    res.add("stairs", subject, "flight plus landing fits the stair room", f"{going:.2f} in {long_:.2f}",
            going <= long_ + TOL)


# ---- doors and windows ----

def _shared_edge(a, b):
    return a.boundary.intersection(b.boundary)


def _segment_at(line, p, width):
    """Length of the straight piece of `line` that contains point p (0 if p is not on it)."""
    parts = list(line.geoms) if hasattr(line, "geoms") else [line]
    for part in parts:
        if part.geom_type == "LineString" and part.distance(p) <= TOL:
            t = part.project(p)
            return min(t, part.length - t) * 2  # room either side of the centre
    return 0.0


def _openings(res, floor, tag, footprint, carpet, req):
    rooms = {r.id: r for r in floor.rooms}
    enclosed = unary_union([r.shape for r in floor.rooms if r.type not in OPEN_AIR])
    outline = enclosed.boundary  # edges onto open air: the outer face, or a balcony
    seen = Counter()
    doors_of = defaultdict(list)
    for d in floor.doors:
        a, b = d.between
        subject = f"{tag} {d.id} ({a}-{b})"
        unknown = [x for x in (a, b) if x != "outside" and x not in rooms]
        if unknown:
            res.add("doors", subject, "door leads to rooms that exist", f"unknown {unknown}", False)
            continue
        if a == b:
            res.add("doors", subject, "door joins two different spaces", a, False)
            continue
        p = Point(d.at)
        if "outside" in (a, b):
            r = rooms[a if b == "outside" else b]
            edge = r.shape.boundary.intersection(outline)
        else:
            edge = _shared_edge(rooms[a].shape, rooms[b].shape)
        room_for_door = _segment_at(edge, p, d.width)
        res.add("doors", subject, "door is on the wall the two spaces share, with room for its width",
                f"{room_for_door:.2f} m for {d.width} m", room_for_door >= d.width - TOL)
        seen[frozenset((a, b))] += 1
        for x in (a, b):
            if x != "outside":
                doors_of[x].append(d)
        types = {rooms[x].type for x in (a, b) if x != "outside"}
        if types & {"bath_wc", "wc"} and "kitchen" in types:
            wet = next(x for x in (a, b) if x != "outside" and rooms[x].type in ("bath_wc", "wc"))
            rq = req.get(rooms[wet].type, {}).get(("opens_to_kitchen", "eq"))
            res.add("doors", subject, "WC does not open directly into the kitchen", "opens into kitchen", False,
                    rq["source"] if rq else None)
        if d.main:
            for (q, _), rq in req.get("exit_door", {}).items():
                actual = d.width if q == "width" else d.height
                res.add("doors", subject + " main entrance", _label(rq), actual, _cmp(actual, rq), rq["source"])
        elif "outside" not in (a, b) and d.kind == "door":
            need = min(DOOR_MIN.get(rooms[x].type, DOOR_DEFAULT_MIN) for x in (a, b))
            res.add("doors", subject, f"door width >= {need} m", d.width, d.width >= need - TOL,
                    note="internal door widths are practice; NBC fixes only the exit doorway")
    for pair, n in seen.items():
        if n > 1:
            res.add("doors", f"{tag} {'-'.join(sorted(pair))}", "one door between the same two spaces",
                    n, False, note="redundant door")
    mains = [d for d in floor.doors if d.main]
    if floor.level == 0:
        res.add("doors", tag, "one main entrance from outside", len(mains),
                len(mains) == 1 and "outside" in mains[0].between)
    for r in floor.rooms:
        if r.type != "stair" or doors_of[r.id]:
            res.add("doors", f"{tag} {r.id} ({r.type})", "room has a door", len(doors_of[r.id]),
                    len(doors_of[r.id]) >= 1)

    # windows
    win_of = defaultdict(list)
    for w in floor.windows:
        subject = f"{tag} {w.id} ({w.room})"
        if w.room not in rooms:
            res.add("windows", subject, "window belongs to a room that exists", w.room, False)
            continue
        edge = rooms[w.room].shape.boundary.intersection(outline)
        fit = _segment_at(edge, Point(w.at), w.width)
        ok = res.add("windows", subject, "window is on an external wall of its room, with room for its width",
                     f"{fit:.2f} m for {w.width} m", fit >= w.width - TOL,
                     note="a window on an internal wall opens into another room")
        if ok:
            win_of[w.room].append(w)
    for r in floor.rooms:
        el = ELEMENT.get(r.type)
        subject = f"{tag} {r.id} ({r.type})"
        key = "habitable_room/other" if el == "habitable_room" else el
        reqs = req.get(key, {})
        rq = reqs.get(("opening_ratio", "ge"))
        if rq:
            area = sum(w.width * w.height for w in win_of[r.id])
            ratio = area / carpet[r.id] if carpet[r.id] else 0.0
            res.add("windows", subject, _label(rq), ratio, _cmp(ratio, rq), rq["source"],
                    note=f"{area:.2f} m2 of windows on {carpet[r.id]:.2f} m2")
        if el in ("bath_wc", "bathroom", "wc"):
            ext = r.shape.boundary.intersection(outline).length
            rq = reqs.get(("external_wall", "eq"))
            res.add("windows", subject, "at least one wall opens to external air", round(ext, 3), ext > TOL,
                    rq["source"] if rq else None)
            best = max(win_of[r.id], key=lambda w: w.width * w.height, default=None)
            for q in ("ventilator_area", "ventilator_side"):
                rq = reqs.get((q, "ge"))
                if not rq:
                    continue
                actual = 0.0 if best is None else (best.width * best.height if q == "ventilator_area"
                                                   else min(best.width, best.height))
                res.add("windows", subject, _label(rq), actual, _cmp(actual, rq), rq["source"])


# ---- reachability ----

def _reachability(res, layout):
    """Every room reachable from outside through doors; floors join through overlapping stair rooms."""
    graph = defaultdict(set)
    nodes = set()
    for f in layout.floors:
        for r in f.rooms:
            nodes.add((f.level, r.id))
        for d in f.doors:
            a, b = ((f.level, x) if x != "outside" else "outside" for x in d.between)
            graph[a].add(b)
            graph[b].add(a)
    floors = sorted(layout.floors, key=lambda f: f.level)
    for lo, hi in zip(floors, floors[1:]):
        for s1 in (r for r in lo.rooms if r.type == "stair"):
            for s2 in (r for r in hi.rooms if r.type == "stair"):
                if s1.shape.intersection(s2.shape).area > 0.5 * min(s1.shape.area, s2.shape.area):
                    graph[(lo.level, s1.id)].add((hi.level, s2.id))
                    graph[(hi.level, s2.id)].add((lo.level, s1.id))
    seen, todo = {"outside"}, ["outside"]
    while todo:
        for n in graph[todo.pop()]:
            if n not in seen:
                seen.add(n)
                todo.append(n)
    cut = sorted(f"L{lv} {rid}" for lv, rid in nodes - seen)
    res.add("circulation", "all floors", "every room reachable from the entrance through doors and stairs",
            cut or "all reachable", not cut, note="fire and life safety: one continuous way out (NBC Part 4 intent)")


# ---- structure: stair and column continuity, spans ----

def _structure(res, floor, tag, footprint, prev):
    cols = floor.columns
    if not cols:
        res.skip("structure", tag, "column positions, spans and continuity", "no columns in the layout")
    else:
        pts = [Point(c.at) for c in cols]
        corners = [Point(p) for p in _corners(footprint)]
        missing = [(round(p.x, 2), round(p.y, 2)) for p in corners
                   if min(p.distance(q) for q in pts) > max(c.width for c in cols)]
        res.add("structure", tag, "a column at every corner of the footprint", missing or "all", not missing)
        long_spans = []
        for axis in (0, 1):
            lines = defaultdict(list)
            for c in cols:
                lines[round(c.at[1 - axis], 2)].append(c.at[axis])
            for key, xs in lines.items():
                xs.sort()
                long_spans += [(key, round(b - a, 2)) for a, b in zip(xs, xs[1:]) if b - a > MAX_SPAN + TOL]
        res.add("structure", tag, f"column spacing on each grid line <= {MAX_SPAN} m", long_spans or "all within",
                not long_spans)
        inside = [c.id for c in cols if not footprint.buffer(TOL).contains(Point(c.at))]
        res.add("structure", tag, "columns inside the footprint", inside or "all", not inside)
    if prev is None:
        return
    if cols:
        below = [Point(c.at) for c in prev.columns]
        floating = [c.id for c in cols if not below or min(Point(c.at).distance(q) for q in below) > TOL]
        res.add("structure", tag, f"every column stands on a column of {prev.name or prev.level}", floating or "all",
                not floating, note="columns of the upper floor must continue down to the foundation")
    up = [r.shape for r in floor.rooms if r.type == "stair"]
    down = [r.shape for r in prev.rooms if r.type == "stair"]
    if up or down:
        same = bool(up and down) and all(
            max(u.intersection(d).area / max(u.area, d.area) for d in down) >= 0.9 for u in up)
        res.add("structure", tag, f"stair is in the same place as on {prev.name or prev.level}", same, same)


def _corners(poly):
    out = []
    geoms = list(poly.geoms) if hasattr(poly, "geoms") else [poly]
    for g in geoms:
        c = list(g.exterior.coords)[:-1]
        n = len(c)
        for i in range(n):
            (x0, y0), (x1, y1), (x2, y2) = c[i - 1], c[i], c[(i + 1) % n]
            if abs((x1 - x0) * (y2 - y1) - (y1 - y0) * (x2 - x1)) > 1e-9:  # not collinear
                out.append((x1, y1))
    return out


# ---- programme, building, areas, materials ----

def _programme_check(res, layout, spec):
    want = _programme(spec)
    have = Counter(ELEMENT.get(r.type, r.type) for f in layout.floors for r in f.rooms)
    for el, n in sorted(want.items()):
        res.add("programme", el, f"at least {n} as in the brief", have.get(el, 0), have.get(el, 0) >= n)
    stairs = sum(1 for f in layout.floors if any(r.type == "stair" for r in f.rooms))
    if len(layout.floors) > 1:
        res.add("programme", "staircase", "a stair on every floor", f"{stairs} of {len(layout.floors)}",
                stairs == len(layout.floors))


def _building(res, layout, req, areas):
    for name, actual, el in (("plinth", layout.plinth_height, "plinth"), ("parapet", layout.parapet_height, "parapet")):
        reqs = req.get(el, {})
        if actual is None:
            if reqs:
                res.skip("building", name, f"{name} height", "not given in the layout")
            continue
        for _, rq in reqs.items():
            res.add("building", name, _label(rq), actual, _cmp(actual, rq), rq["source"])
    total = sum(f["built_up"] for f in areas["floors"])
    areas["built_up_total"] = round(total, 3)
    areas["carpet_total"] = round(sum(f["carpet"] for f in areas["floors"]), 3)
    if areas["plot_area"]:
        far = total / areas["plot_area"]
        areas["far"] = round(far, 3)
        rq = req.get("plot", {}).get(("far", "le"))
        if rq:
            res.add("planning", "building", _label(rq), far, _cmp(far, rq), rq["source"],
                    note="FAR status in the spec: " + rq["source"].get("status", "?"))


def _materials(res, layout, req):
    m = layout.materials
    rc = req.get("rc_concrete", {}).get(("grade", "ge"))
    if rc:
        if m.concrete_grade is None:
            res.skip("materials", "concrete", _label(rc), "concrete grade not given")
        else:
            res.add("materials", "concrete", _label(rc), m.concrete_grade, _cmp(m.concrete_grade, rc), rc["source"])
    el = req.get("reinforcement", {}).get(("elongation", "ge"))
    if el:
        if m.steel_elongation is None:
            res.skip("materials", "reinforcement", _label(el), "elongation not given")
        else:
            res.add("materials", "reinforcement", _label(el), m.steel_elongation, _cmp(m.steel_elongation, el),
                    el["source"])
    ps = req.get("reinforcement", {}).get(("proof_stress", "eq"))
    if ps:
        allowed = [float(x) for x in ps["value"].replace("or", ",").split(",") if x.strip()]
        if m.steel_proof_stress is None:
            res.skip("materials", "reinforcement", "proof stress grade", "steel grade not given")
        else:
            res.add("materials", "reinforcement", f"proof stress one of {ps['value']} MPa", m.steel_proof_stress,
                    m.steel_proof_stress in allowed, ps["source"])


def format_report(result):
    lines = [f"Layout {result['layout']}: {'PASS' if result['pass'] else 'FAIL'} "
             f"({result['summary']['checks']} checks, {result['summary']['failures']} failures, "
             f"{result['summary']['practice_warnings']} warnings, {result['summary']['skipped']} skipped)"]
    for r in result["results"]:
        if r["pass"] is False:
            src = r.get("source", {})
            cite = f" [{src.get('clause')}]" if src else f" [{r['basis']}]"
            lines.append(f"  {'warn' if r['basis'] == 'practice' else 'FAIL'} {r['category']}: {r['subject']}: "
                         f"{r['check']} -> {r['actual']}{cite}")
    a = result["areas"]
    for f in a["floors"]:
        lines.append(f"  {f['floor']}: built-up {f['built_up']:.2f} m2, carpet {f['carpet']:.2f} m2 ({f['carpet_basis']})")
    if a.get("far") is not None:
        lines.append(f"  FAR {a['far']:.3f} on a {a['plot_area']:.2f} m2 plot")
    return "\n".join(lines)
