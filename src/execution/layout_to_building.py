"""Approved layout + designed frame -> building JSON, the single source of truth for Revit.

Deterministic, no LLM. Structure (levels, columns, beams) comes from the designed STAAD
frame, footings from the footing design, architecture from the layout:
- walls on their centrelines; a wall within ON_LINE of a grid line is put exactly on it so it
  sits on its beam, and wall ends at the outer face of the building move to the centreline
  grid like the columns; walls rise to the soffit of the beam above them, partitions between
  grid lines to the underside of the slab;
- doors and windows hosted on the wall they lie on;
- slabs: the ground floor on grade under the ground storey, then at every level the slab over
  the storey below (so a terrace is part of the slab), open where the stair arrives (a hole, or a
  notch when the stair is on the building edge);
- rooms by a point inside each room;
- parapets on roof and terrace edges, and the stair cabin (mumty) on the roof: walls on the roof
  beams around the stair well, a door where the top flight arrives, a roof slab and a room;
- a dog-leg stair in each storey's stair room (see `stair_geometry`).
"""

from shapely.geometry import Point
from shapely.ops import unary_union

from src.execution.building_schema import SCHEMA_VERSION
from src.execution.frame_to_building import FOUNDATION, GROUND_SLAB, level_id, structure_from_frame
from src.spatial.layout import EXTERNAL_WALL, derive_walls
from src.structure.from_layout import ON_LINE

CABIN_DOOR = (0.9, 2.1)  # m, width x height
# NBC 2016 Part 3 cl. 12.18.1.3 (riser <= 0.19 m) and cl. 12.18.1.2 (tread >= 0.25 m), residential
STAIR_MAX_RISER, STAIR_MIN_TREAD = 0.19, 0.25
WALL_TYPES = [
    {"id": "EXT230", "name": "STRATA External 230 Brick + Plaster", "layers": [
        {"material": "Plaster", "thickness": 0.015, "function": "Finish1"},
        {"material": "Brick", "thickness": 0.23, "function": "Structure"},
        {"material": "Plaster", "thickness": 0.012, "function": "Finish2"}]},
    {"id": "INT115", "name": "STRATA Internal 115 Brick + Plaster", "layers": [
        {"material": "Plaster", "thickness": 0.012, "function": "Finish2"},
        {"material": "Brick", "thickness": 0.115, "function": "Structure"},
        {"material": "Plaster", "thickness": 0.012, "function": "Finish2"}]},
]


def _snap(v, lines):
    for g in lines:
        if abs(g - v) <= ON_LINE:
            return g, True
    return v, False


def _along(v, raw_map):
    """A wall end at a raw grid coordinate moves with that grid line (outer face -> centreline)."""
    for raw, new in raw_map.items():
        if abs(raw - v) < 1e-3:
            return new
    return v


def _polygon(geom):
    return _ring(geom.simplify(1e-4).exterior)


def _ring(ring):
    return [(round(x, 4), round(y, 4)) for x, y in list(ring.coords)[:-1]]


def _thickness(type_id):
    return sum(layer["thickness"] for layer in next(t for t in WALL_TYPES if t["id"] == type_id)["layers"])


def _side_inset(walls, level, axis, line, lo, hi):
    """Half the thickness of the wall standing on a side of the stair room (internal if none is found)."""
    for w in walls:
        if w["level"] != level:
            continue
        (x0, y0), (x1, y1) = w["start"], w["end"]
        if axis == "x" and abs(x0 - line) < 1e-3 and abs(x1 - line) < 1e-3:
            a, b = sorted((y0, y1))
        elif axis == "y" and abs(y0 - line) < 1e-3 and abs(y1 - line) < 1e-3:
            a, b = sorted((x0, x1))
        else:
            continue
        if min(b, hi) - max(a, lo) > 0.05:
            return _thickness(w["type"]) / 2
    return _thickness("INT115") / 2


def stair_geometry(bounds, insets, spec, rise, door_side=None, outer_sides=()):
    """Dog-leg stair inside a stair room: (runs, landing, width, riser, notes).

    bounds (x0, x1, y0, y1): the room on wall centrelines; insets {"W","E","S","N"}: half wall
    thicknesses. The flights run along the axis that fits them, starting and arriving at the near
    end (the side with the room's door, else a side inside the building) so every floor is reached
    from inside; flight 1 goes up along one long wall, a landing spans the far end, flight 2 comes
    back along the other wall. The flights fill the clear width up to the layout's flight width."""
    x0, x1, y0, y1 = bounds
    cx0, cx1, cy0, cy1 = x0 + insets["W"], x1 - insets["E"], y0 + insets["S"], y1 - insets["N"]
    total = max(2, round(rise / spec.riser))
    riser = rise / total
    first = total // 2
    run = (first - 1) * spec.tread
    notes = []
    options = []
    for axis, (ua, ub), (va, vb), ends in (("x", (cx0, cx1), (cy0, cy1), ("W", "E")),
                                            ("y", (cy0, cy1), (cx0, cx1), ("S", "N"))):
        width = min(spec.flight_width, (vb - va) / 2)
        landing = (ub - ua) - (total - first - 1) * spec.tread
        if landing < 0.5 or width < 0.6:
            continue
        near = door_side if door_side in ends else next((e for e in ends if e not in outer_sides), ends[0])
        score = (door_side in ends, min(landing, width), width)
        options.append((score, axis, (ua, ub), (va, vb), near, width, landing))
    if not options:
        raise ValueError(f"the stair does not fit in the {cx1 - cx0:.2f} x {cy1 - cy0:.2f} m clear room")
    _, axis, (ua, ub), (va, vb), near, width, _ = max(options, key=lambda o: o[0])
    s = 1 if near in ("W", "S") else -1
    u0, far = (ua, ub) if s == 1 else (ub, ua)
    second = total - first
    turn = u0 + s * run  # top of flight 1
    turn2 = u0 + s * (second - 1) * spec.tread  # bottom of flight 2
    depth = abs(far - max(turn, turn2, key=lambda u: s * u))
    if depth < width - 1e-6:
        notes.append(f"landing {depth:.3f} m deep, less than the {width:.2f} m flight width")

    def xy(u, v):
        return (round(u, 4), round(v, 4)) if axis == "x" else (round(v, 4), round(u, 4))

    lane1, lane2 = va + width / 2, vb - width / 2
    runs = [{"start": xy(u0, lane1), "end": xy(turn, lane1), "risers": first},
            {"start": xy(turn2, lane2), "end": xy(u0, lane2), "risers": second}]
    edge = max(turn, turn2, key=lambda u: s * u)
    landing = [xy(edge, va), xy(far, va), xy(far, vb), xy(edge, vb)]
    arrival = {"side": near, "at": xy(u0, lane2)}
    return runs, {"boundary": landing, "height": round(first * riser, 4)}, round(width, 4), round(riser, 5), \
        arrival, notes


def building_from_layout(layout, model, outcome, footings=(), code_note="", name=None) -> dict:
    frame, results = outcome.frame, outcome.results
    xs, zs = model.xs, model.zs
    floors = sorted(layout.floors, key=lambda f: f.level)
    n = len(floors)
    h = frame.storey_height
    slab = frame.loads.slab_thickness
    b = {"schema_version": SCHEMA_VERSION, "name": name or layout.name}
    b.update(structure_from_frame(frame, results, code_note))
    b["wall_types"] = WALL_TYPES

    walls, openings, rooms = [], [], []
    for k, f in enumerate(floors):
        lvl = level_id(k)
        beam_above = frame.sections[f"beam-{k + 1}"].depth
        hosts = []
        for w in (f.walls if f.walls is not None else derive_walls(f)):
            (x0, y0), (x1, y1) = w.start, w.end
            if abs(y0 - y1) < 1e-3:  # along x
                y, on = _snap(y0, zs)
                start, end = (_along(x0, model.shift["x"]), y), (_along(x1, model.shift["x"]), y)
            elif abs(x0 - x1) < 1e-3:
                x, on = _snap(x0, xs)
                start, end = (x, _along(y0, model.shift["y"])), (x, _along(y1, model.shift["y"]))
            else:
                start, end, on = (x0, y0), (x1, y1), False
            if abs(start[0] - end[0]) + abs(start[1] - end[1]) < 0.05:
                continue
            height = round(h - (beam_above if on else slab), 4)
            wid = f"{lvl}-W{len(walls) + 1}"
            ext = w.external or w.thickness >= EXTERNAL_WALL - 1e-6
            walls.append({"id": wid, "level": lvl, "type": "EXT230" if ext else "INT115",
                          "start": (round(start[0], 4), round(start[1], 4)), "end": (round(end[0], 4), round(end[1], 4)),
                          "height": height, "external": bool(w.external)})
            hosts.append((w, walls[-1]))
        for o in list(f.doors) + list(f.windows):
            p = Point(o.at)
            best = min(hosts, key=lambda t: t[0].line.distance(p), default=None)
            if best is None or best[0].line.distance(p) > ON_LINE + 0.01:
                continue
            spec = best[1]
            (sx, sy), (ex, ey) = spec["start"], spec["end"]
            length = ((ex - sx) ** 2 + (ey - sy) ** 2) ** 0.5
            t = ((o.at[0] - sx) * (ex - sx) + (o.at[1] - sy) * (ey - sy)) / length
            width = min(o.width, length)
            offset = round(min(max(t - width / 2, 0.0), length - width), 4)
            is_door = not hasattr(o, "room")
            height = min(o.height, spec["height"]) if is_door else o.height
            sill = 0.0 if is_door else min(o.sill, max(0.0, spec["height"] - o.height))
            openings.append({"id": f"{lvl}-{'D' if is_door else 'WIN'}{len(openings) + 1}",
                             "kind": "door" if is_door else "window", "wall": spec["id"], "offset": offset,
                             "width": round(width, 4), "height": round(height, 4), "sill": round(sill, 4)})
        for r in f.rooms:
            pt = r.shape.representative_point()
            rooms.append({"id": f"{lvl}-R{len(rooms) + 1}", "level": lvl, "name": r.name or r.type.replace("_", " "),
                          "point": (round(pt.x, 4), round(pt.y, 4)), "stair": r.type == "stair"})

    slabs = []
    ground = unary_union([r.shape for r in floors[0].rooms])
    slabs.append({"id": "F0", "level": level_id(0), "boundary": _polygon(ground), "thickness": GROUND_SLAB})
    for k in range(1, n + 1):
        below = floors[k - 1]
        foot = unary_union([r.shape for r in below.rooms])
        well = unary_union([r.shape for r in below.rooms if r.type == "stair"])
        # A stair well on the building edge becomes a notch in the boundary (Revit rejects a hole that
        # touches the outline); one inside the slab stays a hole.
        cut = foot.difference(well) if not well.is_empty else foot
        if cut.geom_type != "Polygon":
            cut = max(getattr(cut, "geoms", [cut]), key=lambda g: g.area)
        slabs.append({"id": f"F{k}", "level": level_id(k), "boundary": _polygon(cut), "thickness": slab,
                      "openings": [_ring(i) for i in cut.interiors]})
    # Parapets on roof and terrace edges
    par_h = layout.parapet_height if layout.parapet_height is not None else 1.0
    for lvl, a, c in model.parapets:
        walls.append({"id": f"{level_id(lvl)}-W{len(walls) + 1}", "level": level_id(lvl), "type": "EXT230",
                      "start": a, "end": c, "height": par_h, "external": True, "parapet": True})

    # Stairs, one per storey in its stair room
    stairs, arrival = [], None
    for k, f in enumerate(floors):
        cells = model.stair[k]
        room = next((r for r in f.rooms if r.type == "stair" and r.stair), None)
        if not cells or room is None:
            continue
        bx0, bx1 = xs[min(i for i, _ in cells)], xs[max(i for i, _ in cells) + 1]
        by0, by1 = zs[min(j for _, j in cells)], zs[max(j for _, j in cells) + 1]
        lvl = level_id(k)
        insets = {"W": _side_inset(walls, lvl, "x", bx0, by0, by1), "E": _side_inset(walls, lvl, "x", bx1, by0, by1),
                  "S": _side_inset(walls, lvl, "y", by0, bx0, bx1), "N": _side_inset(walls, lvl, "y", by1, bx0, bx1)}
        foot = unary_union([r.shape for r in f.rooms]).boundary
        outer = {sd for sd, line in (("W", ((bx0, by0), (bx0, by1))), ("E", ((bx1, by0), (bx1, by1))),
                                     ("S", ((bx0, by0), (bx1, by0))), ("N", ((bx0, by1), (bx1, by1))))
                 if foot.distance(Point(line[0])) < EXTERNAL_WALL and foot.distance(Point(line[1])) < EXTERNAL_WALL
                 and foot.distance(Point((line[0][0] + line[1][0]) / 2, (line[0][1] + line[1][1]) / 2)) < EXTERNAL_WALL}
        door_side = None
        for d in f.doors:
            x, y = d.at
            for sd, on in (("W", abs(x - bx0) <= ON_LINE and by0 < y < by1), ("E", abs(x - bx1) <= ON_LINE and by0 < y < by1),
                           ("S", abs(y - by0) <= ON_LINE and bx0 < x < bx1), ("N", abs(y - by1) <= ON_LINE and bx0 < x < bx1)):
                if on:
                    door_side = sd
        runs, landing, width, riser, arrival, st_notes = stair_geometry(
            (bx0, bx1, by0, by1), insets, room.stair, h, door_side, outer)
        stairs.append({"id": f"ST{k}", "base_level": lvl, "top_level": level_id(k + 1), "riser": riser,
                       "tread": room.stair.tread, "width": width, "max_riser": STAIR_MAX_RISER,
                       "min_tread": STAIR_MIN_TREAD, "runs": runs, "landings": [landing], "notes": st_notes})

    # Stair cabin on the roof: walls on the beams around the well, a door where the top flight arrives
    if model.cabin:
        lvl = level_id(n)
        cabin_h = round(h - frame.sections[f"beam-{n + 1}"].depth, 4)
        first_cabin = len(walls)
        for _, a, c in model.cabin_walls:
            walls.append({"id": f"{lvl}-W{len(walls) + 1}", "level": lvl, "type": "EXT230", "start": a, "end": c,
                          "height": cabin_h, "external": True})
        if arrival is not None:
            ax, ay = arrival["at"]
            vertical = arrival["side"] in ("W", "E")
            for w in walls[first_cabin:]:
                (sx, sy), (ex, ey) = w["start"], w["end"]
                if vertical and abs(sx - ex) < 1e-6 and abs(sx - ax) <= EXTERNAL_WALL:
                    t, length = ay - sy, ey - sy
                elif not vertical and abs(sy - ey) < 1e-6 and abs(sy - ay) <= EXTERNAL_WALL:
                    t, length = ax - sx, ex - sx
                else:
                    continue
                if length < 0:
                    t, length = -t, -length
                if not 0 < t < length:
                    continue
                width = min(CABIN_DOOR[0], length)
                openings.append({"id": f"{lvl}-D{len(openings) + 1}", "kind": "door", "wall": w["id"],
                                 "offset": round(min(max(t - width / 2, 0.0), length - width), 4),
                                 "width": width, "height": min(CABIN_DOOR[1], cabin_h), "sill": 0.0})
                break
        cx0 = min(xs[i] for i, _ in model.cabin)
        cx1 = max(xs[i + 1] for i, _ in model.cabin)
        cy0 = min(zs[j] for _, j in model.cabin)
        cy1 = max(zs[j + 1] for _, j in model.cabin)
        e = EXTERNAL_WALL / 2
        slabs.append({"id": f"F{n + 1}", "level": level_id(n + 1), "thickness": slab, "openings": [],
                      "boundary": [(round(cx0 - e, 4), round(cy0 - e, 4)), (round(cx1 + e, 4), round(cy0 - e, 4)),
                                   (round(cx1 + e, 4), round(cy1 + e, 4)), (round(cx0 - e, 4), round(cy1 + e, 4))]})
        rooms.append({"id": f"{lvl}-R{len(rooms) + 1}", "level": lvl, "name": "Stair cabin",
                      "point": (round((cx0 + cx1) / 2, 4), round((cy0 + cy1) / 2, 4)), "stair": True})

    b["walls"], b["openings"], b["rooms"], b["floors"] = walls, openings, rooms, slabs
    b["stairs"] = stairs
    nodes = frame.nodes
    b["footings"] = [{"id": f"FT{ft.joint}", "at": (round(ft.at[0], 4), round(ft.at[1], 4)), "level": FOUNDATION,
                      "width": ft.width, "length": ft.length, "thickness": ft.thickness,
                      "design": {"columns": [(nodes[j][0], nodes[j][2]) for j in ft.joints],
                                 "service_kn": ft.service_kn, "governing": ft.governing,
                                 "bearing_kn_m2": ft.pressure, "allowed_kn_m2": ft.allowed}}
                     for ft in footings]
    return b
