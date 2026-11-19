"""Phase 6: plumbing, electrical and finishing layers for the building JSON (LOD 200, no LLM).

Works only from the building JSON (rooms with type and polygon, walls, openings, slabs), so every
fixture lands in the coordinates the Revit builder uses. Rooms are rectilinear (the layout GA
makes rectangles); wall faces are the room's centreline edges moved in by half the wall there.

- plumbing: fixtures per wet room (NBC Part 9), soil and waste stacks on the external wall face,
  one overhead tank sized from the household (Part 9 Section 1 cl. 4.1, 4.4.10)
- electrical: lights, fans, sockets, a switch board per room and a distribution board per floor,
  grouped into sub-circuits within Part 8 Section 2 cl. 5.4.4.3, load densities checked (cl. 5.9.2)
- finishing: floor, wall, dado, skirting and ceiling per room type
"""

from __future__ import annotations

import math

from shapely.geometry import LineString, Point, Polygon

from src.services import templates as T

DOOR_MARGIN = 0.15  # m kept clear each side of a door for wall fixtures
END_MARGIN = 0.3  # m from a room corner
VENT_ABOVE = 1.5  # m, stack vent above the roof it passes (assumed)
STACK_D = {"soil": 0.110, "waste": 0.075}
OUTSIDE_GAP = 0.05  # m between the external wall face and the stack


# ---------------------------------------------------------------- tank (also used by the structure stage)

def household(persons: int | None, bedrooms: int | None) -> tuple[int, int]:
    """(persons the services are designed for, NBC population for the bedroom count)."""
    b = bedrooms or 1
    nbc = T.POPULATION_BY_BEDROOMS.get(b, T.POPULATION_4_PLUS if b >= 4 else 4)
    return max(persons or 0, nbc), nbc


def tank_size(persons: int | None, bedrooms: int | None, demand_lphd: float = 150.0,
              demand_source: dict | None = None) -> dict:
    design, nbc = household(persons, bedrooms)
    need = design * demand_lphd * T.OHT_FRACTION
    fits = [s for s in sorted(T.TANK_SIZES) if s >= need - 1e-6]
    cap = fits[0] if fits else max(T.TANK_SIZES)
    dia, height = T.TANK_SIZES[cap]
    return {"persons": design, "nbc_population": nbc, "brief_persons": persons, "demand_lphd": demand_lphd,
            "required_l": round(need), "capacity_l": cap, "diameter": dia, "height": height,
            "weight_kn": round(cap * 9.81 / 1000 + T.TANK_SELF_WEIGHT, 2),
            "sources": [T.SOURCES["population"], T.SOURCES["overhead_tank"]] + ([demand_source] if demand_source else [])}


def tank_from_brief(brief, spec=None) -> dict:
    persons = getattr(getattr(brief, "family", None), "persons", None)
    prog = getattr(brief, "programme", None)
    bedrooms = getattr(prog, "bedrooms", None)
    demand, src = 150.0, None
    for e in (spec or {}).get("services", []):
        if e.get("quantity") == "water_demand":
            demand, src = float(e["value"]), e.get("source")
    return tank_size(persons, bedrooms, demand, src)


# ---------------------------------------------------------------- geometry helpers

def _type_thickness(building):
    return {t["id"]: sum(layer["thickness"] for layer in t["layers"]) for t in building.get("wall_types", [])}


class Face:
    """One wall face of a room: from a to b (plan), `normal` points into the room."""

    def __init__(self, a, b, normal, external):
        self.a, self.b, self.normal, self.external = a, b, normal, external
        self.length = math.dist(a, b)
        self.dir = ((b[0] - a[0]) / self.length, (b[1] - a[1]) / self.length) if self.length else (1.0, 0.0)
        self.blocked: list[tuple[float, float]] = []  # (t0, t1) along the face

    def at(self, t, off=0.0):
        return (self.a[0] + self.dir[0] * t + self.normal[0] * off, self.a[1] + self.dir[1] * t + self.normal[1] * off)

    def along_x(self):
        return abs(self.dir[0]) > 0.5

    def t_of(self, p):
        return (p[0] - self.a[0]) * self.dir[0] + (p[1] - self.a[1]) * self.dir[1]

    def free(self, margin=END_MARGIN):
        """Free intervals along the face, corners and blocked spans removed."""
        spans = sorted(self.blocked)
        out, cur = [], margin
        for t0, t1 in spans:
            if t0 > cur:
                out.append((cur, min(t0, self.length - margin)))
            cur = max(cur, t1)
        if cur < self.length - margin:
            out.append((cur, self.length - margin))
        return [(a, b) for a, b in out if b - a > 0.05]


def _openings_xy(building):
    """Each opening's centre point, width, kind and level, from its host wall and offset."""
    walls = {w["id"]: w for w in building["walls"]}
    out = []
    for o in building.get("openings", []):
        w = walls.get(o["wall"])
        if not w:
            continue
        (sx, sy), (ex, ey) = w["start"], w["end"]
        length = math.dist((sx, sy), (ex, ey))
        t = o["offset"] + o["width"] / 2
        out.append({"at": (sx + (ex - sx) * t / length, sy + (ey - sy) * t / length), "width": o["width"],
                    "kind": o["kind"], "level": w["level"], "sill": o.get("sill", 0.0), "height": o["height"]})
    return out


def room_faces(room, building, thick, openings):
    poly = Polygon(room["polygon"])
    if not poly.exterior.is_ccw:
        poly = Polygon(list(poly.exterior.coords)[::-1])
    coords = list(poly.exterior.coords)[:-1]
    walls = [w for w in building["walls"] if w["level"] == room["level"] and not w.get("parapet")]
    faces = []
    for i in range(len(coords)):
        a, b = coords[i], coords[(i + 1) % len(coords)]
        edge = LineString([a, b])
        if edge.length < 0.2:
            continue
        nx, ny = -(b[1] - a[1]) / edge.length, (b[0] - a[0]) / edge.length  # left normal: inside for CCW
        host = [w for w in walls if LineString([w["start"], w["end"]]).buffer(0.02).intersection(edge).length
                > 0.5 * edge.length]
        half = thick.get(host[0]["type"], 0.14) / 2 if host else 0.07
        ext = bool(host and host[0].get("external"))
        fa = (a[0] + nx * half, a[1] + ny * half)
        fb = (b[0] + nx * half, b[1] + ny * half)
        f = Face(fa, fb, (nx, ny), ext)
        # trim the ends by the neighbouring walls' half thickness (approximate: same half)
        if f.length > 2 * half + 0.1:
            f = Face(f.at(half), f.at(f.length - half), (nx, ny), ext)
        f.centre_line = edge
        f.half = half
        for o in openings:
            if o["level"] != room["level"] or edge.distance(Point(o["at"])) > 0.05:
                continue
            t = f.t_of(o["at"])
            f.blocked.append((t - o["width"] / 2 - DOOR_MARGIN, t + o["width"] / 2 + DOOR_MARGIN))
            f.doors = getattr(f, "doors", []) + ([o] if o["kind"] == "door" else [])
            f.windows = getattr(f, "windows", []) + ([o] if o["kind"] == "window" else [])
        faces.append(f)
    return faces


def _box(kind, centre, along_x, z, size=None):
    (dx, dy, dz), _, ifc, predefined = T.FIXTURE[kind]
    if size:
        dx, dy, dz = size
    sx, sy = (dx, dy) if along_x else (dy, dx)
    return {"at": (round(centre[0], 4), round(centre[1], 4)), "z": round(z, 4),
            "size": (round(sx, 3), round(sy, 3), round(dz, 3)), "ifc_class": ifc, "predefined": predefined}


def _wall_item(kind, face, t, z, use=""):
    (dx, dy, dz), _, _, _ = T.FIXTURE[kind]
    c = face.at(t, dy / 2)
    face.blocked.append((t - dx / 2 - 0.05, t + dx / 2 + 0.05))
    item = _box(kind, c, face.along_x(), z)
    if use:
        item["use"] = use
    return item


def _spread(faces, n, width, margin=END_MARGIN):
    """n positions (face, t) spread over the free wall length, longest free spans first."""
    spans = [(f, a, b) for f in faces for a, b in f.free(margin) if b - a >= width]
    spans.sort(key=lambda s: -(s[2] - s[1]))
    if not spans or n <= 0:
        return []
    total = sum(b - a for _, a, b in spans)
    counts = [max(0, round(n * (b - a) / total)) for _, a, b in spans]
    i = 0
    while sum(counts) < n:
        counts[i % len(counts)] += 1
        i += 1
    while sum(counts) > n:
        j = max(range(len(counts)), key=lambda k: counts[k])
        counts[j] -= 1
    out = []
    for (f, a, b), k in zip(spans, counts):
        out += [(f, a + (b - a) * (i + 0.5) / k) for i in range(k)]
    return out


# ---------------------------------------------------------------- generator

def prefs_from_brief(brief) -> dict:
    """The client's finish and electrical choices (defaults filled by Brief.with_defaults) and whether the
    household needs accessible switch and socket heights."""
    from src.brief.schema import FINISH_TIERS, ElectricalPrefs, electrical_from
    fam = getattr(brief, "family", None)
    fin = getattr(brief, "finishes", None) or FINISH_TIERS.get(getattr(brief, "finish_level", None) or "standard")
    el = getattr(brief, "electrical", None) or electrical_from(getattr(brief, "electrical_loads", None))
    if isinstance(el, dict):
        el = ElectricalPrefs(**el)
    src = getattr(brief, "sources", {}) or {}
    return {"finishes": fin.model_dump(), "electrical": el.model_dump(),
            "accessible": bool(fam and (fam.elderly or fam.disabled)),
            "from_client": sorted(k for k, v in src.items() if v == "client" and k.split(".")[0] in
                                  ("finishes", "electrical", "finish_level", "electrical_loads"))}


def add_services(building: dict, tank: dict, prefs=None) -> dict:
    """Add fixtures, pipes, tank, circuits, finishes, checks and notes to `building` (in place) and return it.

    `prefs`: prefs_from_brief(brief); a list of A12 strings ("AC in bedrooms") is still accepted."""
    from src.brief.schema import FINISH_TIERS, electrical_from
    if prefs is None or isinstance(prefs, (list, tuple)):
        prefs = {"finishes": FINISH_TIERS["standard"].model_dump(),
                 "electrical": electrical_from(prefs if prefs is not None else
                                               ["AC in bedrooms", "geyser in bathrooms"]).model_dump(),
                 "accessible": False, "from_client": []}
    el, fp = prefs["electrical"], prefs["finishes"]
    accessible = prefs.get("accessible", False)
    corner = T.ACCESSIBLE["corner"] if accessible else END_MARGIN
    switch_z = T.ACCESSIBLE["switchboard"] if accessible else T.FIXTURE["switchboard"][1]
    thick = _type_thickness(building)
    openings = _openings_xy(building)
    elev = {lv["id"]: lv["elevation"] for lv in building["levels"]}
    order = sorted(elev, key=elev.get)
    slab_at = {f["level"]: f["thickness"] for f in building["floors"]}

    fixtures, finishes, notes = [], [], []
    stacks_raw = []  # (kind, (x, y), level)

    def add(kind, room, item):
        item.update({"id": f"{room['level']}-{kind.upper()}{len(fixtures) + 1}", "kind": kind,
                     "system": T.SYSTEM[kind], "room": room["id"], "level": room["level"],
                     "load_w": T.POINT_LOAD.get(kind, 0)})
        fixtures.append(item)
        return item

    def clear_height(level):
        i = order.index(level)
        if i + 1 >= len(order):
            return 3.0
        above = order[i + 1]
        return elev[above] - elev[level] - slab_at.get(above, 0.125)

    for room in building["rooms"]:
        rtype = room.get("type")
        if not rtype or not room.get("polygon"):
            continue
        poly = Polygon(room["polygon"])
        faces = room_faces(room, building, thick, openings)
        if not faces:
            continue
        door_faces = [f for f in faces if getattr(f, "doors", [])]
        door = door_faces[0].doors[0]["at"] if door_faces else faces[0].at(faces[0].length / 2)
        inner = poly.buffer(-0.12, join_style=2)
        x0, y0, x1, y1 = (inner if not inner.is_empty else poly).bounds
        area = poly.area

        # ---- plumbing
        kinds = T.PLUMBING.get(rtype, [])
        if kinds:
            back = max(faces, key=lambda f: Point(door).distance(LineString([f.a, f.b])))
            ends = sorted((0.0, back.length), key=lambda t: -math.dist(back.at(t), door))  # far end from door first
            if "wc" in kinds:
                tw = 0.45 if ends[0] == 0.0 else back.length - 0.45
                add("wc", room, _wall_item("wc", back, tw, 0.0))
                stacks_raw.append(("soil", back, tw, room))
            if "shower" in kinds:
                ts = back.length - 0.45 if ends[0] == 0.0 else 0.45
                if back.length < 1.55:
                    notes.append(f"{room['name']} ({room['id']}): back wall {back.length:.2f} m, shower and WC close")
                add("shower", room, _box("shower", back.at(ts, 0.45), back.along_x(), 0.0))
                back.blocked.append((ts - 0.5, ts + 0.5))
            if "washbasin" in kinds:
                sides = [f for f in faces if f is not back and abs(f.dir[0] * back.dir[0] + f.dir[1] * back.dir[1]) < 0.5]
                spot = _spread(sides or [f for f in faces if f is not back], 1, 0.6)
                if spot:
                    f, t = spot[0]
                    add("washbasin", room, _wall_item("washbasin", f, t, T.FIXTURE["washbasin"][1]))
            if "kitchen_sink" in kinds:
                wf = [f for f in faces if getattr(f, "windows", [])]
                if wf:
                    f, t = wf[0], wf[0].t_of(wf[0].windows[0]["at"])
                    f.blocked = [s for s in f.blocked if not (s[0] < t < s[1])]
                else:
                    f, t = _spread(faces, 1, 0.7)[0]
                add("kitchen_sink", room, _wall_item("kitchen_sink", f, t, T.FIXTURE["kitchen_sink"][1]))
                stacks_raw.append(("waste", f, t, room))
            if "washing_machine" in kinds:
                t = 0.4 if ends[0] == 0.0 else back.length - 0.4
                add("washing_machine", room, _wall_item("washing_machine", back, t, 0.0))
                stacks_raw.append(("waste", back, t, room))

        # ---- electrical
        e = T.ELECTRICAL.get(rtype)
        if e:
            h = clear_height(room["level"])
            if door_faces:
                f = door_faces[0]
                td = f.t_of(door)
                w = door_faces[0].doors[0]["width"]
                cand = [td + w / 2 + DOOR_MARGIN + 0.12, td - w / 2 - DOOR_MARGIN - 0.12]
                t = next((c for c in cand if corner / 2 < c < f.length - corner / 2), f.length / 2)
                add("switchboard", room, _wall_item("switchboard", f, t, switch_z))
            else:
                spot = _spread(faces, 1, 0.3, corner)
                if spot:
                    add("switchboard", room, _wall_item("switchboard", spot[0][0], spot[0][1], switch_z))
            n6 = e["socket_6a"] + (el["extra_sockets"] if rtype in T.HABITABLE else 0)
            z6 = e.get("socket_height", T.ACCESSIBLE["socket"] if accessible else 0.3)
            for f, t in _spread(faces, n6, 0.2, corner):
                add("socket_6a", room, _wall_item("socket_6a", f, t, z6))
            if rtype in el["tv_rooms"]:
                spot = _spread(faces, 1, 0.2, corner)
                if spot:
                    add("socket_6a", room, _wall_item("socket_6a", spot[0][0], spot[0][1],
                                                      T.ACCESSIBLE["socket"] if accessible else T.TV_HEIGHT, use="tv"))
            power = [(u, z) for u, z in e["power"] if u not in ("ac", "geyser")]
            if rtype in el["ac_rooms"]:
                power.append(("ac", 2.1))
            if rtype in T.BATHS - {"wc"} and el["geyser"]:
                power.append(("geyser", 1.8))
            for use, z in power:
                if use == "ac":
                    for f in faces:
                        for wn in getattr(f, "windows", []):
                            tw = f.t_of(wn["at"])
                            f.blocked.append((tw - wn["width"] / 2 - 0.1, tw + wn["width"] / 2 + 0.1))
                spot = _spread(faces, 1, 0.2, corner if use not in ("ac", "geyser") else END_MARGIN)
                if spot:
                    add("socket_16a", room, _wall_item("socket_16a", spot[0][0], spot[0][1], z, use=use))
            fans = e["fans"] + (1 if e["fans"] and area > T.BIG_ROOM_SECOND_FAN else 0)
            long_x = (x1 - x0) >= (y1 - y0)
            fan_pts = []
            for i in range(fans):
                fr = (i + 0.5) / fans
                fan_pts.append((x0 + (x1 - x0) * fr, (y0 + y1) / 2) if long_x else ((x0 + x1) / 2, y0 + (y1 - y0) * fr))
            for p in fan_pts:
                dz = T.FIXTURE["fan"][0][2]
                add("fan", room, _box("fan", p, True, h - T.CEILING_CLEARANCE["fan"] - dz))
            nl = max(1, math.ceil(area / e["m2_per_light"]))
            if rtype == "stair":  # no ceiling over the well: a bracket light on the far wall
                f, t = _spread(faces, 1, 0.3)[0] if _spread(faces, 1, 0.3) else (faces[0], faces[0].length / 2)
                add("light", room, _wall_item("light", f, t, 2.2))
            else:
                cols = max(1, round(math.sqrt(nl * (x1 - x0) / max(y1 - y0, 0.1))))
                rows = max(1, math.ceil(nl / cols))
                k = 0
                for r in range(rows):
                    for c in range(cols):
                        if k >= nl:
                            break
                        p = (x0 + (x1 - x0) * (c + 0.5) / cols, y0 + (y1 - y0) * (r + 0.5) / rows)
                        if any(math.dist(p, q) < 0.7 for q in fan_pts):
                            p = (p[0] + (0.75 if long_x else 0), p[1] + (0 if long_x else 0.75))
                        add("light", room, _box("light", p, True, h - T.CEILING_CLEARANCE["light"] - 0.05))
                        k += 1

        # ---- finishes
        fin = T.FINISHES.get(rtype)
        if fin:
            (fmat, fth), wall, dado, skirt = fin
            wall = T.PAINT_TEXT.get(fp.get("paint"), wall) if rtype not in ("balcony",) else wall
            pick = fp.get("floor_living") if rtype in T.LIVING_GROUP else fp.get("floor_bedrooms") if rtype in T.BED_GROUP else None
            if pick:
                fmat, fth = T.FLOOR_TEXT[pick], 0.05
                skirt = f"100 mm {pick} skirting"
            if rtype in T.BATHS and fp.get("bath_dado") is not None:
                dado = (dado[0], fp["bath_dado"]) if fp["bath_dado"] > 0 else None
            finishes.append({"room": room["id"], "level": room["level"], "floor": fmat, "floor_thickness": fth,
                             "wall": wall, "dado": dado[0] if dado else None, "dado_height": dado[1] if dado else 0.0,
                             "skirting": skirt, "ceiling": T.CEILING,
                             "finish_floor": not room.get("stair"), "polygon": [tuple(p) for p in room["polygon"]]})

    # ---- distribution board per floor: in the largest corridor, else beside the stair, else the largest room
    boards = {}
    for lvl in order:
        rooms = [r for r in building["rooms"] if r["level"] == lvl and r.get("type") and r.get("polygon")]
        if not rooms or all(r["type"] == "stair" for r in rooms):  # roof with only the stair cabin: fed from below
            continue
        pick = (max((r for r in rooms if r["type"] == "corridor"), key=lambda r: Polygon(r["polygon"]).area, default=None)
                or next((r for r in rooms if r["type"] == "stair"), None)
                or max(rooms, key=lambda r: Polygon(r["polygon"]).area))
        faces = room_faces(pick, building, thick, openings)
        for f in faces:  # keep clear of what is already on these walls
            for fx in fixtures:
                if fx["room"] == pick["id"] and LineString([f.a, f.b]).distance(Point(fx["at"])) < 0.2:
                    t = f.t_of(fx["at"])
                    f.blocked.append((t - 0.2, t + 0.2))
        spot = _spread(faces, 1, 0.5)
        if spot:
            boards[lvl] = add("distribution_board", pick, _wall_item("distribution_board", spot[0][0], spot[0][1],
                                                                     T.FIXTURE["distribution_board"][1]))

    # ---- sub-circuits per floor (cl. 5.4.4.3)
    circuits = []
    for lvl in order:
        pts = [f for f in fixtures if f["level"] == lvl]
        if not pts:
            continue
        light, power = [], []
        for f in pts:
            if f["kind"] in ("light", "fan", "socket_6a"):
                if (not light or len(light[-1]) >= T.LIGHT_CIRCUIT_POINTS
                        or sum(x["load_w"] for x in light[-1]) + f["load_w"] > T.LIGHT_CIRCUIT_W):
                    light.append([])
                light[-1].append(f)
            elif f["kind"] == "socket_16a":
                dedicated = f.get("use") in ("ac", "geyser")
                if dedicated or not power or len(power[-1]) >= T.POWER_CIRCUIT_OUTLETS or power[-1][0].get("use") in ("ac", "geyser"):
                    power.append([])
                power[-1].append(f)
        for kind, groups in (("lighting", light), ("power", power)):
            for g in groups:
                cid = f"{lvl}-C{len([c for c in circuits if c['level'] == lvl]) + 1}"
                for f in g:
                    f["circuit"] = cid
                feed = next((boards[x] for x in reversed(order[:order.index(lvl) + 1]) if x in boards), None)
                circuits.append({"id": cid, "level": lvl, "kind": kind, "board": feed["id"] if feed else None,
                                 "points": [f["id"] for f in g], "load_w": sum(f["load_w"] for f in g)})

    # ---- stacks: on the external wall face outside the wet room, merged up the building
    pipes = []
    for kind, face, t, room in stacks_raw:
        d = STACK_D[kind]
        below = [Polygon(fl["boundary"]) for fl in building["floors"] if elev[fl["level"]] <= elev[room["level"]]]

        def outside(f, tt):
            q = f.at(min(max(tt, 0.15), f.length - 0.15), -(f.half * 2 + OUTSIDE_GAP + d / 2))
            return q if not any(b.buffer(-0.01).contains(Point(q)) for b in below) else None

        # on an external face whose outside is open air down to the ground (not over a lower floor)
        cands = [face] + [f for f in room_faces(room, building, thick, openings) if f is not face]
        p, ext = None, True
        for f in cands:
            if f.external:
                tt = t if f is face else f.t_of(face.at(t))
                p = outside(f, tt)
                if p:
                    break
        if p is None:  # internal duct against the fixture's wall
            p, ext = face.at(min(max(t, 0.15), face.length - 0.15), d / 2 + 0.02), False
            notes.append(f"{room['name']} ({room['id']}): no external wall open to the ground, {kind} stack in an "
                         "internal duct through the floors below")
        hit = next((s for s in pipes if s["kind"] == kind and math.dist(s["at"], p) < 0.8), None)
        # vent above the highest roof over this room (NBC two-pipe: stacks carried up as vents)
        roofs = [elev[fl["level"]] for fl in building["floors"] if Polygon(fl["boundary"]).contains(Point(room["point"]))]
        top = max(roofs + [elev[room["level"]]]) + VENT_ABOVE
        if hit:
            hit["z1"] = max(hit["z1"], top)
            hit["serves"].append(room["id"])
        else:
            pipes.append({"id": f"P{len(pipes) + 1}", "kind": kind, "at": (round(p[0], 4), round(p[1], 4)),
                          "z0": 0.0, "z1": round(top, 3), "diameter": d, "outside": ext, "serves": [room["id"]]})
    for s in pipes:
        s["z1"] = round(s["z1"], 3)

    # ---- overhead tank on the highest slab (the stair cabin roof when there is one)
    top_floor = max(building["floors"], key=lambda f: elev[f["level"]])
    tb = Polygon(top_floor["boundary"])
    c = tb.centroid
    tanks = [{"id": "T1", "level": top_floor["level"], "at": (round(c.x, 4), round(c.y, 4)),
              "capacity_l": tank["capacity_l"], "diameter": tank["diameter"], "height": tank["height"],
              "weight_kn": tank["weight_kn"], "persons": tank["persons"], "required_l": tank["required_l"]}]
    if min(tb.bounds[2] - tb.bounds[0], tb.bounds[3] - tb.bounds[1]) < tank["diameter"] + 0.2:
        notes.append(f"tank {tank['diameter']} m across is tight on the {top_floor['level']} slab")

    # ---- checks
    checks = []

    def check(name, ok, detail, source):
        checks.append({"check": name, "pass": bool(ok), "detail": detail, "source": source})

    have = {k: sum(f["kind"] == k for f in fixtures) for k in T.FIXTURE}
    check("dwelling amenities: WC, washbasin, kitchen sink, bathing place",
          all(have[k] for k in ("wc", "washbasin", "kitchen_sink", "shower")),
          f"WC {have['wc']}, washbasin {have['washbasin']}, sink {have['kitchen_sink']}, shower {have['shower']}",
          T.SOURCES["amenities"])
    for room in building["rooms"]:
        want = T.PLUMBING.get(room.get("type"), [])
        got = sorted(f["kind"] for f in fixtures if f["room"] == room["id"] and f["system"] == "plumbing")
        if want:
            check(f"{room['name']} ({room['id']}) fitments", sorted(want) == got, ", ".join(got) or "none",
                  T.SOURCES["fitments"])
    for lvl in order:
        rooms = [r for r in building["rooms"] if r["level"] == lvl and r.get("polygon") and r.get("type") != "stair"]
        a = sum(Polygon(r["polygon"]).area for r in rooms)
        if not a:
            continue
        lw = sum(f["load_w"] for f in fixtures if f["level"] == lvl and f["kind"] == "light")
        pw = sum(f["load_w"] for f in fixtures if f["level"] == lvl and f["kind"] in ("socket_6a", "socket_16a"))
        check(f"{lvl} lighting load <= {T.LIGHTING_W_PER_M2_MAX:g} W/m2", lw / a <= T.LIGHTING_W_PER_M2_MAX,
              f"{lw} W over {a:.1f} m2 = {lw / a:.1f} W/m2", T.SOURCES["lighting_density"])
        check(f"{lvl} power provision >= {T.POWER_W_PER_M2_MIN:g} W/m2", pw / a >= T.POWER_W_PER_M2_MIN,
              f"{pw} W over {a:.1f} m2 = {pw / a:.1f} W/m2 connected", T.SOURCES["lighting_density"])
    sockets = [f for f in fixtures if f["kind"] in ("socket_6a", "socket_16a")]
    low = [f["id"] for f in sockets if f["z"] < 0.2 - 1e-9]
    check("sockets above 200 mm from the floor", not low, f"{len(sockets) - len(low)} of {len(sockets)}"
          + (f"; low: {low[:4]}" if low else ""), T.SOURCES["socket_height"])
    if accessible:
        reach = [f for f in fixtures if f["kind"] == "switchboard"
                 or (f["kind"] == "socket_6a" and f.get("use") != "tv" and f["room"] not in
                     {r["id"] for r in building["rooms"] if r.get("type") == "kitchen"})]
        out = [f["id"] for f in reach if not 0.8 - 1e-9 <= f["z"] <= 1.1 + 1e-9]
        check("switches and sockets within 800-1100 mm (elderly or wheelchair users)", not out,
              f"{len(reach) - len(out)} of {len(reach)}" + (f"; outside: {out[:4]}" if out else ""),
              T.SOURCES["accessible"])
    bad = [c for c in circuits if (c["kind"] == "lighting" and (len(c["points"]) > T.LIGHT_CIRCUIT_POINTS
                                                                 or c["load_w"] > T.LIGHT_CIRCUIT_W))
           or (c["kind"] == "power" and len(c["points"]) > T.POWER_CIRCUIT_OUTLETS)]
    check("sub-circuits within ten points / 800 W, two 16 A outlets", not bad,
          f"{len(circuits)} circuits" + (f"; over: {[c['id'] for c in bad]}" if bad else ""), T.SOURCES["subcircuit"])
    check(f"overhead tank >= half a day's demand", tank["capacity_l"] >= tank["required_l"],
          f"{tank['capacity_l']} l for {tank['persons']} persons x {tank['demand_lphd']:g} l/day x "
          f"{T.OHT_FRACTION:g} = {tank['required_l']} l", T.SOURCES["overhead_tank"])

    notes += [
        f"Overhead tank {tank['capacity_l']} l ({tank['diameter']} m dia x {tank['height']} m) on the "
        f"{top_floor['level']} slab, {tank['weight_kn']} kN full: designed for {tank['persons']} persons "
        f"(brief {tank['brief_persons']}, NBC population {tank['nbc_population']} for the bedrooms).",
        f"{len(pipes)} stacks ({sum(p['kind'] == 'soil' for p in pipes)} soil, "
        f"{sum(p['kind'] == 'waste' for p in pipes)} waste), two-pipe system; pipe routing not modelled (LOD 300).",
        "Points per room and loads per point are STRATA defaults (assumed); finishes and AC/TV/geyser points follow "
        "the brief" + (f" (client gave: {', '.join(prefs['from_client'])})" if prefs.get("from_client") else
                       " (finish level and appliance defaults, matrix A11/A12)") + ".",
        ("Accessible heights for the elderly/wheelchair users in the household: switch boards 1.0 m, sockets 0.8 m, "
         "0.6 m from internal corners (NBC 2016 Part 8 Sec 2 cl. 7.2.3)." if accessible else
         "Sockets 0.3 m above the floor (NBC 2016 Part 8 Sec 2 cl. 7.2.3: above 200 mm); switch boards 1.2 m (assumed)."),
    ]
    building["fixtures"], building["pipes"], building["tanks"] = fixtures, pipes, tanks
    building["circuits"], building["finishes"] = circuits, finishes
    building["services"] = {"checks": checks, "notes": notes, "tank": {k: v for k, v in tank.items() if k != "sources"},
                            "sources": T.SOURCES}
    return building
