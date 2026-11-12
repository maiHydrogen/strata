"""Approved layout -> STAAD frame, with every load taken from the layout.

Deterministic, no LLM. The structural grid is the layout's column grid, which the layout
stage keeps identical on every floor. A storey's frame covers the grid cells that the
storey actually builds, so an upper floor that leaves a terrace has columns and beams only
under its own rooms, and the terrace is a roof with a parapet.

Frame coordinates: STAAD X = plan x, STAAD Z = plan y, STAAD Y up, y = 0 at ground-floor
level (top of the plinth). Layouts put the outer face of the building on the outer grid
lines; those lines move in by half the external wall so columns and beams sit on wall
centrelines. Levels:
- the base: fixed supports at the top of the footings, FOOTING_TOP below ground-floor level;
- level 0: plinth beams tying the columns and carrying the ground-floor walls (the ground
  floor itself is a slab on grade, outside the frame);
- levels 1..n: floor and roof beams. A beam carries the walls standing on it; slab panels
  carry their own weight, finishes, imposed load and the partitions that stand between grid lines;
- a stair cabin (mumty) over the stair well when the stair reaches the roof: the stair cell's
  columns rise one more storey to a cabin roof, and the cabin walls stand on the roof beams.

Loads, with the clause or assumption each comes from, are listed in FrameModel.notes.
"""

import math
from dataclasses import dataclass, field

from shapely.geometry import LineString, Point
from shapely.ops import unary_union

from src.spatial.layout import EXTERNAL_WALL, Layout, derive_walls
from src.structure import wind
from src.structure.sizing import initial_sizes, slab_thickness
from src.structure.std_writer import Frame, Loads, Member, Panel, SeismicParams

FOOTING_TOP = 1.5  # m below ground-floor level: footings about 1.5 m below ground under a 0.45 m plinth (assumed)
MASONRY = 20.0  # kN/m3, burnt clay brick masonry (IS 875 Part 1 Table 1)
PLASTER = 0.5  # kN/m2 of wall face: 12 to 15 mm cement plaster on both faces (IS 875 Part 1)
FLOOR_FINISH = 1.0  # kN/m2, screed and tiles
ROOF_FINISH = 1.5  # kN/m2, waterproofing, screed and tiles on a flat roof (practice)
STAIR_WAIST = 0.15  # m, waist slab of a dog-leg flight
ON_LINE = 0.13  # m: a wall whose centreline is this close to a grid line stands on that line's beam
PARTITION_MIN = 1.0  # kN/m2, IS 875 Part 2 cl. 3.1.2
NOMINAL_BEAM = 0.45  # m, beam depth assumed when working out how much of a wall the openings take
# Imposed loads when the spec has none (NBC 2016 Part 6 Table 1 (i), Table 2 (i); same as IS 875 Part 2)
LIVE_DEFAULTS = {"room": 2.0, "circulation": 3.0, "roof": 1.5}
CABIN_ROOF_LIVE = 0.75  # kN/m2, flat roof without access except for maintenance (IS 875 Part 2 Table 2)
CIRCULATION = {"corridor", "stair", "store", "balcony", "foyer"}  # corridors, passages, staircases, stores, balconies
TOL = 1e-6


@dataclass
class FrameModel:
    frame: Frame
    xs: list  # grid lines along X (plan x), frame coordinates
    zs: list  # grid lines along Z (plan y)
    cells: list  # per storey: set of grid cells (i, j) it builds
    stair: list  # per storey: cells holding the stair
    shift: dict  # raw plan coordinate -> frame coordinate, per axis ("x", "y")
    notes: list = field(default_factory=list)
    cabin: set = field(default_factory=set)  # grid cells of the stair cabin on the roof (empty: none)
    parapets: list = field(default_factory=list)  # (level, (x0, y0), (x1, y1)): parapet runs, frame coordinates
    cabin_walls: list = field(default_factory=list)  # (level, (x0, y0), (x1, y1)): cabin walls on the roof


def imposed_loads(spec):
    """{"room", "circulation", "roof"} -> kN/m2 from the spec's structure entries, and their clauses."""
    loads, sources = dict(LIVE_DEFAULTS), {}
    for e in (spec or {}).get("structure", []):
        if e.get("quantity") != "imposed_load" or not isinstance(e.get("value"), (int, float)):
            continue
        note = (e.get("note") or "").lower()
        if e.get("element") == "roof":
            key = "roof"
        elif "corridor" in note:
            key = "circulation"
        elif "outer edge" in note:  # balconies: 3.0 kN/m2, same as circulation
            continue
        else:
            key = "room"
        loads[key] = e["value"] if key not in sources else max(loads[key], e["value"])
        sources[key] = f"{e['source']['standard']}, {e['source']['clause']}"
    return loads, sources


def site_value(spec, quantity, default):
    for e in (spec or {}).get("site", []):
        if e.get("quantity") == quantity and isinstance(e.get("value"), (int, float)):
            return e["value"], f"{e['source']['standard']}, {e['source']['clause']}"
    return default, "assumed"


def _axis_map(values, lo, hi, inset):
    """Raw grid coordinates -> frame coordinates: the outermost lines move in to the wall centreline."""
    out = {}
    for v in values:
        r = round(v, 3)
        out[r] = round(v + inset if abs(v - lo) < 1e-3 else v - inset if abs(v - hi) < 1e-3 else v, 3)
    return out


def _index(lines, v):
    for i, g in enumerate(lines):
        if abs(g - v) < 1e-3:
            return i
    raise ValueError(f"{v} is not a grid line")


def stair_dead_load(stair):
    """Weight on plan of a dog-leg flight (kN/m2): waist slab on the slope, steps, finish (IS 875 Part 1)."""
    r, t = (stair.riser, stair.tread) if stair else (0.15, 0.25)
    return round(25 * (STAIR_WAIST * math.hypot(r, t) / t + r / 2) + FLOOR_FINISH, 3)


def opening_area(floor, wall, height):
    """Door and window area on `wall` (m2); an open-plan opening takes the full wall height."""
    line = wall.line
    area = 0.0
    for o in list(floor.doors) + list(floor.windows):
        if line.distance(Point(o.at)) > ON_LINE + 0.01:
            continue
        h = height if getattr(o, "kind", "") == "opening" else min(o.height, height)
        area += o.width * h
    return area


def build_frame(layout: Layout, spec=None) -> FrameModel:
    floors = sorted(layout.floors, key=lambda f: f.level)
    n = len(floors)
    h = floors[0].floor_to_floor
    if any(abs(f.floor_to_floor - h) > TOL for f in floors):
        raise ValueError("storeys of different heights are not supported yet")
    if not floors[0].columns:
        raise ValueError("the layout has no columns")
    notes = []
    raw_x = sorted({round(c.at[0], 3) for f in floors for c in f.columns})
    raw_y = sorted({round(c.at[1], 3) for f in floors for c in f.columns})
    gx = [c.at[0] for c in floors[0].columns]
    gy = [c.at[1] for c in floors[0].columns]
    half = EXTERNAL_WALL / 2
    mx = _axis_map(raw_x, min(gx), max(gx), half)
    my = _axis_map(raw_y, min(gy), max(gy), half)
    xs, zs = sorted(set(mx.values())), sorted(set(my.values()))

    # Cells each storey builds: all four corners carry a column of that storey and the centre is inside a room
    cells, stair = [], []
    for f in floors:
        pts = {(_index(xs, mx[round(c.at[0], 3)]), _index(zs, my[round(c.at[1], 3)])) for c in f.columns}
        shapes = [r.shape for r in f.rooms]
        foot = unary_union(shapes)
        built, st = set(), set()
        for i in range(len(xs) - 1):
            for j in range(len(zs) - 1):
                if not {(i, j), (i + 1, j), (i, j + 1), (i + 1, j + 1)} <= pts:
                    continue
                centre = Point((xs[i] + xs[i + 1]) / 2, (zs[j] + zs[j + 1]) / 2)
                if foot.contains(centre):
                    built.add((i, j))
                    if any(r.type == "stair" and r.shape.contains(centre) for r in f.rooms):
                        st.add((i, j))
        cells.append(built)
        stair.append(st)
    if not cells[0]:
        raise ValueError("no grid cell of the ground floor is enclosed by columns")
    for k in range(1, n):
        over = cells[k] - cells[k - 1]
        if over:
            raise ValueError(f"{floors[k].name or k}: cells {sorted(over)} have no storey below them")

    # Stair cabin: over the stair cells of the top storey, which reach the roof
    cabin = set(stair[n - 1])
    top = n + 1 if cabin else n  # highest beam level

    def corners(cs):
        return {p for (i, j) in cs for p in ((i, j), (i + 1, j), (i, j + 1), (i + 1, j + 1))}

    def edges(cs):
        out = {}
        for (i, j) in cs:
            for e in (((i, j), (i + 1, j)), ((i, j + 1), (i + 1, j + 1)), ((i, j), (i, j + 1)), ((i + 1, j), (i + 1, j + 1))):
                out.setdefault(e, []).append((i, j))
        return out

    # Nodes: base, level 0 (plinth), levels 1..n, and the cabin roof
    level_cells = {-1: cells[0], 0: cells[0], **{k: cells[k - 1] for k in range(1, n + 1)}}
    elevation = {-1: -FOOTING_TOP, **{k: round(k * h, 3) for k in range(top + 1)}}
    if cabin:
        level_cells[n + 1] = cabin
    storey_cells = cells + ([cabin] if cabin else [])
    frame = Frame(storey_height=h, storeys=top, base_y=-FOOTING_TOP, orthogonal=True, service=True,
                  cabin=bool(cabin))
    node = {}
    nid = 1
    for lvl in range(-1, top + 1):
        for (i, j) in sorted(corners(level_cells[lvl]), key=lambda p: (p[1], p[0])):
            frame.nodes[nid] = (xs[i], elevation[lvl], zs[j])
            node[(i, j, lvl)] = nid
            nid += 1

    mid = 1
    for (i, j) in sorted(corners(cells[0]), key=lambda p: (p[1], p[0])):  # footing to plinth
        frame.members[mid] = Member(node[(i, j, -1)], node[(i, j, 0)], "column", 0)
        mid += 1
    for k in range(top):
        for (i, j) in sorted(corners(storey_cells[k]), key=lambda p: (p[1], p[0])):
            frame.members[mid] = Member(node[(i, j, k)], node[(i, j, k + 1)], "column", k)
            mid += 1
    beam_at = {}  # (level, edge) -> member id
    level_edges = {}
    for lvl in range(top + 1):
        es = edges(level_cells[lvl])
        level_edges[lvl] = es
        along_x = sorted((e for e in es if e[0][1] == e[1][1]), key=lambda e: (e[0][1], e[0][0]))
        along_z = sorted((e for e in es if e[0][0] == e[1][0]), key=lambda e: (e[0][0], e[0][1]))
        for a, b in along_x + along_z:
            frame.members[mid] = Member(node[(*a, lvl)], node[(*b, lvl)], "beam", lvl)
            beam_at[(lvl, (a, b))] = mid
            mid += 1

    # Materials and imposed loads
    mat = layout.materials
    frame.fck = mat.concrete_grade or 25.0
    frame.fy = mat.steel_proof_stress or 500.0
    live, live_src = imposed_loads(spec)
    zone, zone_src = site_value(spec, "zone_factor", 0.36)
    vb, vb_src = site_value(spec, "wind_speed", 50.0)
    frame.seismic = SeismicParams(zone_factor=zone)
    parapet = layout.parapet_height if layout.parapet_height is not None else 1.0
    plinth = layout.plinth_height if layout.plinth_height is not None else 0.45

    spans = [(xs[i + 1] - xs[i], zs[j + 1] - zs[j]) for k in range(n) for (i, j) in cells[k] - stair[k]]
    slab, slab_note = slab_thickness(spans, frame.fy)
    frame.loads = Loads(slab_thickness=slab, wall_thickness=EXTERNAL_WALL, masonry_density=MASONRY,
                        parapet_height=parapet, floor_finish=FLOOR_FINISH, floor_live=live["room"],
                        roof_live=live["roof"])

    # Walls: on a grid line -> the beam under it; between grid lines -> a partition load on the slab panel
    wall_faces, partitions = {}, {}
    wall_height = h - NOMINAL_BEAM
    off_grid_ground = 0
    for k, f in enumerate(floors):
        es = level_edges[k]
        for w in (f.walls if f.walls is not None else derive_walls(f)):
            face = MASONRY * w.thickness + PLASTER
            length = w.line.length
            if length < 0.05:
                continue
            solid = max(0.0, 1 - opening_area(f, w, wall_height) / (length * wall_height))
            (x0, y0), (x1, y1) = w.start, w.end
            placed = 0.0
            if abs(y0 - y1) < 1e-3:  # wall along X
                j = next((jj for jj, z in enumerate(zs) if abs(z - y0) <= ON_LINE), None)
                if j is not None:
                    lo, hi = sorted((x0, x1))
                    for (a, b) in es:
                        if a[1] == b[1] == j:
                            ov = min(hi, xs[b[0]]) - max(lo, xs[a[0]])
                            if ov > 0.01:
                                m = beam_at[(k, (a, b))]
                                wall_faces.setdefault(m, []).append(
                                    (round(face * solid * ov / (xs[b[0]] - xs[a[0]]), 4), "storey"))
                                placed += ov
            elif abs(x0 - x1) < 1e-3:  # wall along Z
                i = next((ii for ii, x in enumerate(xs) if abs(x - x0) <= ON_LINE), None)
                if i is not None:
                    lo, hi = sorted((y0, y1))
                    for (a, b) in es:
                        if a[0] == b[0] == i:
                            ov = min(hi, zs[b[1]]) - max(lo, zs[a[1]])
                            if ov > 0.01:
                                m = beam_at[(k, (a, b))]
                                wall_faces.setdefault(m, []).append(
                                    (round(face * solid * ov / (zs[b[1]] - zs[a[1]]), 4), "storey"))
                                placed += ov
            rest = length - placed
            if rest <= EXTERNAL_WALL + 0.05:  # wall ends reach the outer face, past the centreline grid
                continue
            if k == 0:  # stands on the slab on grade with its own footing
                off_grid_ground += 1
                continue
            mx_, my_ = w.line.interpolate(0.5, normalized=True).coords[0]
            cell = next(((i, j) for (i, j) in cells[k - 1]
                         if xs[i] - TOL <= mx_ <= xs[i + 1] + TOL and zs[j] - TOL <= my_ <= zs[j + 1] + TOL), None)
            if cell is None:
                raise ValueError(f"wall {w.id} of {f.name or k} is over no slab of the storey below")
            per_m = face * solid * (h - slab)
            p = partitions.setdefault((k, cell), {"weight": 0.0, "per_m": 0.0})
            p["weight"] += per_m * rest
            p["per_m"] = max(p["per_m"], face * (h - slab))

    # Parapets on roof and terrace edges; the cabin walls on the roof beams around the stair well
    ext_face = MASONRY * EXTERNAL_WALL + PLASTER
    parapet_edges, cabin_edges = [], []
    for lvl in range(1, n + 1):
        roof_cells = cells[lvl - 1] - (cells[lvl] if lvl < n else cabin)
        for e, owners in level_edges[lvl].items():
            if len(owners) == 1 and owners[0] in roof_cells:
                wall_faces.setdefault(beam_at[(lvl, e)], []).append((ext_face, "parapet"))
                parapet_edges.append((lvl, e))
    for e, owners in edges(cabin).items():
        if len(owners) == 1:  # no openings deducted: the cabin door is small (on the safe side)
            wall_faces.setdefault(beam_at[(n, e)], []).append((ext_face, "storey"))
            cabin_edges.append((n, e))
    frame.wall_faces = wall_faces

    # Slab panels
    stair_dl = stair_dead_load(next((r.stair for f in floors for r in f.rooms if r.stair), None))
    panels = []
    for lvl in range(1, n + 1):
        below = lvl - 1
        for (i, j) in sorted(cells[below], key=lambda c: (c[1], c[0])):
            x0, x1, z0, z1 = xs[i], xs[i + 1], zs[j], zs[j + 1]
            area = (x1 - x0) * (z1 - z0)
            if (i, j) in stair[below]:
                panels.append(Panel(elevation[lvl], x0, x1, z0, z1, stair_dl, live["circulation"], "stair"))
            elif lvl < n and (i, j) in cells[lvl]:
                part = partitions.get((lvl, (i, j)))
                extra = 0.0
                if part:
                    extra = max(PARTITION_MIN, part["per_m"] / 3, part["weight"] / area)
                cell = LineString([(x0, z0), (x1, z1)]).envelope
                types = {r.type for r in floors[lvl].rooms if r.shape.intersection(cell).area > 0.05}
                ll = live["circulation"] if types & CIRCULATION else live["room"]
                panels.append(Panel(elevation[lvl], x0, x1, z0, z1,
                                    round(slab * 25 + FLOOR_FINISH + extra, 3), ll, "floor"))
            else:
                panels.append(Panel(elevation[lvl], x0, x1, z0, z1, round(slab * 25 + ROOF_FINISH, 3),
                                    live["roof"], "roof"))
    for (i, j) in sorted(cabin, key=lambda c: (c[1], c[0])):
        panels.append(Panel(elevation[n + 1], xs[i], xs[i + 1], zs[j], zs[j + 1], round(slab * 25 + ROOF_FINISH, 3),
                            CABIN_ROOF_LIVE, "roof"))
    frame.panels = panels

    # Wind: storey forces on the exposed faces, IS 875 Part 3
    frame.wind = {"X": {}, "Z": {}}
    for lvl in range(top + 1):
        z_above_ground = elevation[lvl] + plinth + (parapet if lvl == n else 0)
        pd = wind.design_pressure(z_above_ground, vb) * wind.NET_CP
        parts = []  # (cells whose faces collect, tributary height)
        if lvl >= 1:
            parts.append((storey_cells[lvl - 1], h / 2))
        if lvl < top:
            parts.append((storey_cells[lvl], h / 2))
        if lvl == n:
            parts.append((cells[n - 1], parapet))
        for cs, height in parts:
            for d in ("X", "Z"):
                for (i, j) in cs:
                    if d == "X" and (i - 1, j) not in cs:  # face looking towards -X, wind blowing +X
                        width, ends = zs[j + 1] - zs[j], ((i, j), (i, j + 1))
                    elif d == "Z" and (i, j - 1) not in cs:
                        width, ends = xs[i + 1] - xs[i], ((i, j), (i + 1, j))
                    else:
                        continue
                    for p in ends:
                        nd = node[(*p, lvl)]
                        frame.wind[d][nd] = frame.wind[d].get(nd, 0.0) + pd * height * width / 2

    initial = initial_sizes(frame)
    beams = sum(m.kind == "beam" for m in frame.members.values())
    notes += [
        f"Grid {len(xs)} x {len(zs)} lines; {len(frame.nodes)} joints, {len(frame.members) - beams} columns, {beams} beams.",
        f"Fixed supports at the top of the footings, {FOOTING_TOP} m below ground-floor level (assumed); "
        f"plinth beams at ground-floor level carry the ground-floor walls.",
        f"Walls: burnt clay brick {MASONRY:g} kN/m3 plus {PLASTER} kN/m2 plaster (IS 875 Part 1), less door and "
        f"window areas; parapets {parapet:g} m on roof and terrace edges.",
        f"Partitions between grid lines: slab load of at least one third of their weight per metre, "
        f"minimum {PARTITION_MIN:g} kN/m2 (IS 875 Part 2 cl. 3.1.2).",
        f"Imposed loads {live['room']:g} (rooms), {live['circulation']:g} (corridors, stairs, stores), "
        f"{live['roof']:g} kN/m2 (roofs): {', '.join(sorted(set(live_src.values()))) or 'NBC 2016 Part 6 defaults'}.",
        f"Stair: {stair_dl:g} kN/m2 on plan ({STAIR_WAIST} m waist, steps, finish) over the stair cell.",
        f"Finishes {FLOOR_FINISH:g} kN/m2 on floors, {ROOF_FINISH:g} kN/m2 on roofs (practice).",
        slab_note,
        f"Seismic: IS 1893 (Part 1):2016, Z = {zone:g} ({zone_src}), R = 5 (SMRF, IS 13920 detailing), I = 1.0, "
        f"medium soil (assumed); 100% + 30% orthogonal combinations (cl. 6.3.4.1).",
        wind.basis(vb) + f"; Vb source: {vb_src}.",
    ] + initial
    if off_grid_ground:
        notes.append(f"{off_grid_ground} ground-floor partition(s) between grid lines stand on the slab on grade.")
    if cabin:
        notes.append(f"Stair cabin over the stair well: {len(corners(cabin))} columns rise one storey ({h:g} m) "
                     f"above the roof; {EXTERNAL_WALL * 1000:.0f} mm brick walls on the roof beams, door area not "
                     f"deducted; cabin roof imposed load {CABIN_ROOF_LIVE:g} kN/m2 (IS 875 Part 2 Table 2, "
                     f"no access except for maintenance).")
    shift = {"x": mx, "y": my}

    def runs(edge_list):
        """Grid edges -> straight runs in frame coordinates, collinear neighbours joined."""
        out = []
        for lvl in sorted({lv for lv, _ in edge_list}):
            segs = sorted(((xs[a[0]], zs[a[1]]), (xs[b[0]], zs[b[1]])) for lv, (a, b) in edge_list if lv == lvl)
            merged = []
            for s in segs:
                for m in merged:
                    if m[1] == s[0] and (m[0][0] == s[1][0] or m[0][1] == s[1][1]):
                        m[1] = s[1]
                        break
                else:
                    merged.append([s[0], s[1]])
            out += [(lvl, tuple(a), tuple(b)) for a, b in merged]
        return out

    return FrameModel(frame, xs, zs, cells, stair, shift, notes, cabin=cabin, parapets=runs(parapet_edges),
                      cabin_walls=runs(cabin_edges))
