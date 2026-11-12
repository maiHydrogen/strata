"""Floor-plan library built from ResPlan (17,000 South Asian residential plans, CC BY 4.0).

Used three ways in the layout stage: retrieve plans similar to the brief to seed the LLM
and the GA, measure realistic room-adjacency statistics for the GA's soft objectives, and
give the rule checker real plans to run on.

ResPlan stores shapely geometry in a 256-unit image frame with each plan's net area in m2;
the metric scale is sqrt(net_area / inner area). Its rooms are drawn to the inside wall
face, so `convert` grows each room by half the wall thickness to reach the wall centreline
(the convention of src/spatial/layout.py), rotates the plan so the entrance is on the
front (y = 0) and moves it to the origin. Window heights are not in ResPlan; 1.2 m on a
0.9 m sill is assumed and marked in the layout's source.

The pickle is third-party data: it is loaded with an allow-list unpickler that only lets
through the three callables it needs (shapely WKB loading and two numpy helpers).
"""

import json
import math
import pickle
from collections import Counter
from pathlib import Path

from shapely import affinity
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.ops import nearest_points, unary_union
from shapely.validation import make_valid

from src.spatial.layout import Door, Floor, Layout, Room, Window

ROOT = Path(__file__).resolve().parents[2]
RESPLAN_PKL = ROOT / "data" / "plan_library" / "resplan" / "raw" / "ResPlan.pkl"
RESPLAN_SPLIT = ROOT / "data" / "plan_library" / "resplan" / "repo" / "split.json"
LIBRARY = ROOT / "data" / "plan_library" / "library.jsonl"
INDEX = ROOT / "data" / "plan_library" / "index.json"

TYPE = {"living": "living", "bedroom": "bedroom", "bathroom": "bath_wc", "kitchen": "kitchen",
        "balcony": "balcony", "storage": "store", "stair": "stair", "parking": "parking"}
MIN_SPACE = 1.0  # m2: leftover pieces of the interior smaller than this are wall junctions, not spaces
MAX_OVERLAP = 0.08  # fraction of floor area two grown rooms both claim (resolved first-come); more = bad drawing
LINE_MIN = 1.5  # m: wall lines at least this long count as structural lines for floor pairing


class _AllowList(pickle.Unpickler):
    OK = {("shapely.io", "from_wkb"), ("numpy._core.multiarray", "scalar"), ("numpy", "dtype")}

    def find_class(self, module, name):
        if (module, name) not in self.OK:
            raise pickle.UnpicklingError(f"ResPlan pickle refers to {module}.{name}, which is not allowed")
        return super().find_class(module, name)


def load_resplan(path=RESPLAN_PKL):
    with open(path, "rb") as f:
        return _AllowList(f).load()


def _parts(g):
    if g is None or g.is_empty:
        return []
    return [p for p in (g.geoms if hasattr(g, "geoms") else [g]) if p.geom_type == "Polygon" and p.area > 0]


def _largest(g):
    ps = _parts(g)
    return max(ps, key=lambda p: p.area) if ps else None


def _long_side(poly):
    c = list(poly.minimum_rotated_rectangle.exterior.coords)
    return max(math.dist(c[0], c[1]), math.dist(c[1], c[2]))


def convert(plan, split=None):
    """ResPlan plan -> (Layout, quality) or (None, reason). Bad source geometry is a reason, not a crash."""
    try:
        return _convert(plan, split)
    except Exception as e:  # GEOS topology errors on invalid drawings
        return None, f"geometry error: {type(e).__name__}"


def _convert(plan, split):
    if not plan.get("net_area") or plan["inner"].is_empty:
        return None, "no metric scale (net_area missing)"
    k = math.sqrt(plan["net_area"] / plan["inner"].area)
    t = plan["wall_depth"] * k

    # orientation: rotate so the entrance side is the front (minimum y)
    shell = unary_union([plan["inner"], plan["wall"]])
    fd = plan.get("front_door")
    angle = 0
    if fd is not None and not fd.is_empty:
        minx, miny, maxx, maxy = shell.bounds
        c = fd.centroid
        side = min((c.y - miny, 0), (maxx - c.x, 90), (maxy - c.y, 180), (c.x - minx, 270))[1]
        angle = side  # rotating by this brings that side to the bottom

    def tf(g):
        g = affinity.scale(g, k, k, origin=(0, 0))
        return affinity.rotate(g, -angle, origin=(0, 0)) if angle else g

    shell = tf(shell)
    dx, dy = -shell.bounds[0], -shell.bounds[1]

    def place(g):
        return affinity.translate(tf(g), dx, dy)

    spaces = []
    raw_rooms = []
    for key, rtype in TYPE.items():
        for poly in _parts(plan.get(key)):
            raw_rooms.append(poly)
            spaces.append((rtype, place(poly).simplify(0.02).buffer(0)))
    rest = place(plan["inner"]).difference(unary_union([p for _, p in spaces]).buffer(0.01))
    for piece in _parts(rest):  # leftover interior: passages; thin slivers are wall junctions
        if piece.area >= MIN_SPACE and not piece.buffer(-0.3, join_style="mitre").is_empty:
            spaces.append(("corridor", piece.simplify(0.02).buffer(0)))
    if not spaces:
        return None, "no rooms"
    # grow each room into the wall around it, never into another room (open-plan rooms touch
    # with no wall between them); what two rooms still both claim goes to the one placed first
    originals = [p for _, p in spaces]
    grown, taken = [], None
    for i, (rt, poly) in enumerate(spaces):
        others = unary_union(originals[:i] + originals[i + 1:])
        g = poly.buffer(t / 2, join_style="mitre").difference(others)
        overlap_here = g.intersection(taken).area if taken is not None else 0.0
        if taken is not None:
            g = g.difference(taken)
        grown.append((rt, g, overlap_here))
        taken = g if taken is None else unary_union([taken, g])
    overlap = sum(o for *_, o in grown)
    if overlap > MAX_OVERLAP * taken.area:
        return None, f"grown rooms overlap {overlap:.2f} m2"
    # external edges go to the outer face of the wall: rooms on the outside take the outer half
    band = affinity.translate(shell, dx, dy).difference(taken)
    out = []
    for rt, g, _ in grown:
        extra = unary_union(_parts(g.buffer(t / 2 + 0.02, join_style="mitre").intersection(band)))
        if not extra.is_empty:
            g = unary_union(_parts(g) + _parts(extra))  # polygons only: stray line pieces have no area
            band = band.difference(extra)
        out.append((rt, g))
    grown = out
    footprint = unary_union([p for _, g in grown for p in _parts(g)])
    # scale check: ResPlan's own gross area (with walls and balconies) should match the converted plan
    gross = plan.get("area") or 0
    if gross and not 0.6 <= footprint.area / gross <= 1.6:
        return None, f"scale inconsistent: {footprint.area:.1f} m2 converted vs {gross:.1f} m2 listed"

    rooms, counters, room_of = [], Counter(), {}  # room_of: index in `spaces` -> room id
    for idx, (rt, g) in enumerate(grown):
        g = _largest(g)
        if g is None:
            continue
        clean = _largest(make_valid(Polygon([(round(x, 3), round(y, 3)) for x, y in g.exterior.coords])))
        if clean is None:
            continue
        counters[rt] += 1
        room_of[idx] = f"{rt}{counters[rt]}"
        coords = [(round(x, 3), round(y, 3)) for x, y in clean.simplify(0.001).exterior.coords[:-1]]
        if len(coords) < 4:
            coords.append(coords[-1])
        rooms.append(Room(id=f"{rt}{counters[rt]}", type=rt, polygon=coords))
    shapes = {r.id: r.shape for r in rooms}
    outline = footprint.boundary

    def touching(g):
        probe = g.buffer(0.6 * t)
        hits = sorted(((s.intersection(probe).area, rid) for rid, s in shapes.items()), reverse=True)
        return [rid for area, rid in hits if area > 1e-4]

    doors = []
    fd_shape = place(fd) if fd is not None and not fd.is_empty else None
    for i, d in enumerate(_parts(plan.get("door")) + ([fd_shape] if fd_shape is not None else [])):
        main = fd_shape is not None and d is fd_shape
        d = d if main else place(d)
        near = touching(d)
        if not near:
            continue
        at = d.centroid
        if main or len(near) == 1:
            at = nearest_points(outline, at)[0]
            between = (near[0], "outside")
        else:
            between = (near[0], near[1])
            shared = shapes[near[0]].boundary.intersection(shapes[near[1]].boundary)
            if not shared.is_empty:  # onto the edge the two rooms share
                at = nearest_points(shared, at)[0]
        doors.append(Door(id=f"D{i + 1}", between=between, at=(round(at.x, 3), round(at.y, 3)),
                          width=round(_long_side(d), 3), main=main))
    # open-plan connections: rooms ResPlan draws touching with no wall between them
    joined = {frozenset(d.between) for d in doors}
    for i in room_of:
        for j in room_of:
            if j <= i or originals[i].distance(originals[j]) > 0.05:
                continue
            a, b = room_of[i], room_of[j]
            if frozenset((a, b)) in joined:
                continue
            shared = shapes[a].boundary.intersection(shapes[b].boundary)
            parts = [g for g in (shared.geoms if hasattr(shared, "geoms") else [shared]) if g.geom_type == "LineString"]
            line = max(parts, key=lambda g: g.length, default=None)
            if line is None or line.length < 0.6:
                continue
            mid = line.interpolate(0.5, normalized=True)
            doors.append(Door(id=f"O{i + 1}_{j + 1}", between=(a, b), at=(round(mid.x, 3), round(mid.y, 3)),
                              width=round(line.length - 0.02, 3), kind="opening"))
    # an open stair (no door, no opening) joins the non-wet room it shares most edge with
    linked = {x for d in doors for x in d.between}
    for r in rooms:
        if r.type != "stair" or r.id in linked:
            continue
        best = max(((shapes[o.id].boundary.intersection(r.shape.boundary).length, o.id) for o in rooms
                    if o.id != r.id and o.type not in ("bath_wc", "store")), default=(0, None))
        if best[0] >= 0.6:
            shared = shapes[best[1]].boundary.intersection(r.shape.boundary)
            parts = [g for g in (shared.geoms if hasattr(shared, "geoms") else [shared]) if g.geom_type == "LineString"]
            line = max(parts, key=lambda g: g.length)
            mid = line.interpolate(0.5, normalized=True)
            doors.append(Door(id=f"O{r.id}", between=(r.id, best[1]), at=(round(mid.x, 3), round(mid.y, 3)),
                              width=round(line.length - 0.02, 3), kind="opening"))
    open_edge = unary_union([r.shape for r in rooms if r.type not in ("balcony", "parking")]).boundary
    windows = []
    for i, w in enumerate(_parts(plan.get("window"))):
        w = place(w)
        near = touching(w)
        if not near:
            continue
        at = nearest_points(open_edge, w.centroid)[0]
        windows.append(Window(id=f"W{i + 1}", room=near[0], at=(round(at.x, 3), round(at.y, 3)),
                              width=round(_long_side(w), 3), height=1.2, sill=0.9))
    floor = Floor(level=0, name="Plan", rooms=rooms, doors=doors, windows=windows)
    layout = Layout(name=f"resplan_{plan['id']}",
                    source={"dataset": "ResPlan", "licence": "CC BY 4.0", "id": int(plan["id"]), "split": split,
                            "scale_m_per_unit": round(k, 5), "wall_thickness": round(t, 3), "rotated_deg": angle,
                            "assumed": ["window height 1.2 m, sill 0.9 m", "rooms grown to wall centrelines"]},
                    floors=[floor])
    return layout, {"overlap_m2": round(overlap, 3), "footprint_m2": round(footprint.area, 3)}


def structural_lines(layout):
    """x and y positions of room edges at least LINE_MIN long: where walls, beams and columns can go."""
    xs, ys = Counter(), Counter()
    for r in layout.floors[0].rooms:
        c = list(r.shape.exterior.coords)
        for (x0, y0), (x1, y1) in zip(c, c[1:]):
            if abs(x0 - x1) < 0.02 and abs(y1 - y0) >= LINE_MIN:
                xs[round(x0 / 0.05) * 0.05] += 1
            elif abs(y0 - y1) < 0.02 and abs(x1 - x0) >= LINE_MIN:
                ys[round(y0 / 0.05) * 0.05] += 1
    return sorted(round(x, 2) for x in xs), sorted(round(y, 2) for y in ys)


def features(layout, quality):
    """One index row: what retrieval and floor pairing compare."""
    f = layout.floors[0]
    footprint = unary_union([r.shape for r in f.rooms])
    minx, miny, maxx, maxy = footprint.bounds
    rooms = {r.id: r for r in f.rooms}
    count = Counter(r.type for r in f.rooms)
    pairs = Counter("-".join(sorted(rooms[x].type if x in rooms else x for x in d.between)) for d in f.doors)
    stair = next((r.shape for r in f.rooms if r.type == "stair"), None)
    main = next((d for d in f.doors if d.main), None)
    xs, ys = structural_lines(layout)
    return {"id": layout.source["id"], "split": layout.source["split"],
            "width": round(maxx - minx, 3), "depth": round(maxy - miny, 3),
            "footprint_area": quality["footprint_m2"], "rooms": dict(count),
            "bedrooms": count.get("bedroom", 0), "bathrooms": count.get("bath_wc", 0),
            "stair": [round(v, 3) for v in stair.bounds] if stair is not None else None,
            "entrance_x": round(main.at[0] / (maxx - minx), 3) if main else None,
            "door_pairs": dict(pairs), "lines_x": xs, "lines_y": ys}


# ---- retrieval and floor pairing ----

def load_index(path=INDEX):
    with open(path) as f:
        return json.load(f)["plans"]


def retrieve(index, width, depth, bedrooms, bathrooms, stair=False, k=5, include_augmented=False, min_fill=0.5):
    """Plans that fit a width x depth envelope (entrance on the front), closest in programme first.

    Score: room-count difference first, then how much of the envelope the plan leaves empty.
    Mirrored plans fit the same envelope, so mirroring is left to the caller.
    """
    out = []
    for p in index:
        if p["split"] == "augmented" and not include_augmented:
            continue
        if p["width"] > width + 0.05 or p["depth"] > depth + 0.05 or (stair and not p["stair"]):
            continue
        if p["width"] * p["depth"] < min_fill * width * depth:
            continue
        rooms_off = abs(p["bedrooms"] - bedrooms) + 0.5 * abs(p["bathrooms"] - bathrooms)
        empty = 1 - (p["width"] * p["depth"]) / (width * depth)
        out.append((rooms_off, round(empty, 3), p["id"], p))
    out.sort(key=lambda x: x[:3])
    return [p for *_, p in out[:k]]


def _match(lines_a, lines_b, tol=0.15):
    if not lines_b:
        return 1.0
    return sum(any(abs(b - a) <= tol for a in lines_a) for b in lines_b) / len(lines_b)


def floor_compatibility(lower, upper, tol=0.15):
    """How well `upper` can sit on `lower` (both index rows, front-left corners aligned).

    The stair must be in the same place (overlap of the stair boxes over their union), the
    upper footprint must fit on the lower one, and the upper floor's wall lines must land on
    the lower floor's wall lines, so its beams and columns continue down instead of
    standing on a slab. Returns the three numbers and whether all pass.
    """
    if not lower["stair"] or not upper["stair"]:
        return {"ok": False, "reason": "both floors need a stair"}
    a, b = Polygon.from_bounds(*lower["stair"]), Polygon.from_bounds(*upper["stair"])
    stair_iou = a.intersection(b).area / a.union(b).area
    fits = upper["width"] <= lower["width"] + tol and upper["depth"] <= lower["depth"] + tol
    lines = min(_match(lower["lines_x"], upper["lines_x"], tol), _match(lower["lines_y"], upper["lines_y"], tol))
    ok = stair_iou >= 0.8 and fits and lines >= 0.8
    return {"ok": ok, "stair_iou": round(stair_iou, 3), "fits": fits, "lines_on_lower": round(lines, 3)}


def adjacency_stats(index):
    """Share of plans in which each pair of room types is joined by a door (for the GA's soft objectives)."""
    n = len(index)
    seen = Counter()
    for p in index:
        for pair in p["door_pairs"]:
            seen[pair] += 1
    return {pair: round(c / n, 4) for pair, c in seen.most_common()}


__all__ = ["load_resplan", "convert", "features", "load_index", "retrieve", "floor_compatibility",
           "adjacency_stats", "structural_lines", "MultiPolygon", "Point"]


# ---- plans as text, for the LLM's layout proposals ----

def load_layouts(ids, path=LIBRARY):
    """Layouts for the given ResPlan ids, read from the library file (one JSON layout per line)."""
    want, out = {int(i) for i in ids}, {}
    with open(path) as f:
        for line in f:
            if not want:
                break
            for i in list(want):
                if f'"id":{i},' in line.replace(" ", ""):
                    layout = Layout.model_validate_json(line)
                    if layout.source.get("id") == i:
                        out[i] = layout
                        want.discard(i)
    return [out[int(i)] for i in ids if int(i) in out]


def describe(layout):
    """One line per plan: its size, then its rooms band by band (front to rear) and side by side."""
    f = layout.floors[0]
    rooms = [r for r in f.rooms if r.type not in ("corridor",)]
    footprint = unary_union([r.shape for r in f.rooms])
    minx, miny, maxx, maxy = footprint.bounds
    w, d = maxx - minx, maxy - miny
    zones = {"front": [], "middle": [], "rear": []}
    for r in rooms:
        c = r.shape.centroid
        y = (c.y - miny) / d
        x = (c.x - minx) / w
        zone = "front" if y < 1 / 3 else "middle" if y < 2 / 3 else "rear"
        side = "left" if x < 0.4 else "right" if x > 0.6 else "centre"
        zones[zone].append((x, f"{r.type.replace('_', ' ')} {r.shape.area:.0f} m2 ({side})"))
    parts = [f"{z}: " + ", ".join(t for _, t in sorted(v)) for z, v in zones.items() if v]
    return f"{w:.1f} x {d:.1f} m, " + "; ".join(parts)
