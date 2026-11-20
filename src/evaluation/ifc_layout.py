"""Read an IFC file back into a STRATA Layout, so the rule checker can score arm A of the ablation.

The baseline (Text2BIM-style) writes IFC only. Its rooms are IfcSpaces, its doors and windows fill
wall openings, and its storeys carry elevations; that is enough for the checker's room, opening,
reachability, travel and programme rules. What the baseline never produces (stair flights, columns)
stays absent and the checker reports it as missing or skipped, as it would for any layout.

Room types come from the space names ("Bedroom 2", "Toilet", "Kitchen"). Doors and windows are
assigned to the rooms whose outlines pass within OPENING_TOL of the opening and snapped onto the
nearest room edge, so a plan whose spaces were drawn to the inner wall faces is not failed for that.
"""

import re

import ifcopenshell
import ifcopenshell.util.placement as up
import ifcopenshell.util.unit as uu
import numpy as np
from shapely.geometry import Point, Polygon
from shapely.ops import nearest_points, unary_union

from src.spatial.layout import Column, Door, Floor, Layout, Room, Window, derive_walls

OPENING_TOL = 0.35  # m: half a thick wall plus a margin
TYPES = [  # first match wins
    (r"stair", "stair"), (r"bed|master|guest room|kids", "bedroom"),
    (r"bath|toilet|\bwc\b|w\.c|washroom|lavatory", "bath_wc"), (r"kitchen", "kitchen"),
    (r"dining", "dining"), (r"living|drawing|hall|lounge|family room|sitting", "living"),
    (r"puja|pooja|prayer|mandir", "puja"), (r"study|office", "study"), (r"store|storage", "store"),
    (r"corridor|passage|lobby|foyer|entrance", "corridor"), (r"balcony|verandah|veranda|sit-out", "balcony"),
    (r"utility|wash area|laundry", "utility"), (r"parking|garage|car porch", "parking"),
]


def room_type(name):
    n = (name or "").lower()
    return next((t for pat, t in TYPES if re.search(pat, n)), None)


SCALE = [1.0]  # project length unit in metres, set per file


def _xy(matrix, pts):
    out = []
    for x, y in pts:
        v = matrix @ np.array([x, y, 0.0, 1.0]) * SCALE[0]
        out.append((round(float(v[0]), 4), round(float(v[1]), 4)))
    return out


def _profile_points(profile):
    if profile.is_a("IfcArbitraryClosedProfileDef"):
        curve = profile.OuterCurve
        if curve.is_a("IfcPolyline"):
            return [tuple(p.Coordinates[:2]) for p in curve.Points]
        if curve.is_a("IfcIndexedPolyCurve"):
            return [tuple(c[:2]) for c in curve.Points.CoordList]
    if profile.is_a("IfcRectangleProfileDef"):
        hx, hy = profile.XDim / 2, profile.YDim / 2
        pts = [(-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)]
        if profile.Position is not None:
            m = up.get_axis2placement(profile.Position)
            pts = [tuple((m @ np.array([x, y, 1.0]))[:2]) if m.shape == (3, 3)
                   else tuple((m @ np.array([x, y, 0.0, 1.0]))[:2]) for x, y in pts]
        return pts
    return None


def space_polygon(space):
    """Plan outline of a space in global coordinates, from its swept-solid profile."""
    if space.Representation is None or space.ObjectPlacement is None:
        return None
    place = up.get_local_placement(space.ObjectPlacement)
    for rep in space.Representation.Representations:
        for item in rep.Items:
            if not item.is_a("IfcExtrudedAreaSolid"):
                continue
            pts = _profile_points(item.SweptArea)
            if not pts:
                continue
            pos = up.get_axis2placement(item.Position) if item.Position is not None else np.eye(4)
            pts = _xy(place @ pos, pts)
            if len(pts) > 1 and pts[0] == pts[-1]:
                pts = pts[:-1]
            if len(pts) == 3:
                pts.append(pts[2])
            return pts if len(pts) >= 4 else None
    return None


def _opening_centre(el):
    m = up.get_local_placement(el.ObjectPlacement)
    k = SCALE[0]
    width = float(el.OverallWidth or 0.9 / k) * k
    o, x = m[:2, 3] * k, m[:2, 0]
    c = o + x * width / 2
    return (float(c[0]), float(c[1])), width, float(el.OverallHeight or 2.1 / k) * k


def _near(rooms, p):
    pt = Point(p)
    d = sorted(((r.shape.exterior.distance(pt), r) for r in rooms), key=lambda t: t[0])
    return [r for dist, r in d if dist <= OPENING_TOL]


def _snap(p, rooms):
    """Nearest point to p on the shared edge of two rooms, or on the one room's outline."""
    pt = Point(p)
    if len(rooms) >= 2:
        edge = rooms[0].shape.exterior.intersection(rooms[1].shape.buffer(0.02).exterior.buffer(0.02))
        target = edge if not edge.is_empty else rooms[0].shape.exterior
    else:
        target = rooms[0].shape.exterior
    q = nearest_points(target, pt)[0]
    return (round(q.x, 4), round(q.y, 4))


def ifc_to_layout(path, name, plot=None):
    """(Layout or None, notes): None when the file has no storey with a readable space."""
    model = ifcopenshell.open(str(path))
    SCALE[0] = uu.calculate_unit_scale(model)
    notes = []
    storeys = sorted(model.by_type("IfcBuildingStorey"), key=lambda s: float(s.Elevation or 0.0))
    elevs = [float(s.Elevation or 0.0) * SCALE[0] for s in storeys]
    floors = []
    for i, st in enumerate(storeys):
        contained = [e for rel in st.ContainsElements or [] for e in rel.RelatedElements]
        aggregated = [e for rel in st.IsDecomposedBy or [] for e in rel.RelatedObjects]
        spaces = [e for e in aggregated + contained if e.is_a("IfcSpace")]
        rooms, seen = [], {}
        for sp in spaces:
            label = sp.LongName or sp.Name or "space"
            t = room_type(label)
            if t is None:
                notes.append(f"{st.Name}: space '{label}' has no recognised room type (read as utility)")
                t = "utility"
            poly = space_polygon(sp)
            if poly is None:
                notes.append(f"{st.Name}: space '{label}' has no readable outline")
                continue
            base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "space"
            seen[base] = seen.get(base, 0) + 1
            rid = base if seen[base] == 1 else f"{base}_{seen[base]}"
            rooms.append(Room(id=rid, type=t, name=label, polygon=poly))
        if not rooms:
            notes.append(f"{st.Name}: no readable spaces")
            continue
        doors, windows = [], []
        for el in contained:
            if el.is_a("IfcDoor") or el.is_a("IfcWindow"):
                if el.ObjectPlacement is None:
                    continue
                c, w, h = _opening_centre(el)
                near = _near(rooms, c)
                if not near:
                    notes.append(f"{st.Name}: {el.is_a()} '{el.Name}' is not next to any room")
                    continue
                at = _snap(c, near)
                if el.is_a("IfcDoor"):
                    between = (near[0].id, near[1].id) if len(near) >= 2 else (near[0].id, "outside")
                    doors.append(Door(id=f"D{len(doors) + 1}", between=between, at=at, width=w, height=h))
                else:
                    windows.append(Window(id=f"W{len(windows) + 1}", room=near[0].id, at=at, width=w,
                                          height=min(h, 2.4), sill=0.9))
        outside = [d for d in doors if d.between[1] == "outside"]
        if not floors and outside:  # the ground floor's widest outside door is the entrance
            max(outside, key=lambda d: d.width).main = True
        cols = []
        for el in contained:
            if el.is_a("IfcColumn") and el.ObjectPlacement is not None:
                m = up.get_local_placement(el.ObjectPlacement)
                k = SCALE[0]
                cols.append(Column(id=f"C{len(cols) + 1}", at=(round(float(m[0, 3]) * k, 4), round(float(m[1, 3]) * k, 4))))
        f2f = elevs[i + 1] - elevs[i] if i + 1 < len(elevs) and elevs[i + 1] > elevs[i] else 3.0
        f = Floor(level=len(floors), name=st.Name or f"L{len(floors)}", floor_to_floor=f2f, rooms=rooms,
                  doors=doors, windows=windows, columns=cols)
        try:
            f.walls = derive_walls(f)
        except Exception as e:  # overlapping or broken spaces: the checker reports the geometry itself
            notes.append(f"{st.Name}: walls could not be derived ({e})")
        floors.append(f)
    if not floors:
        return None, notes
    if plot is None:
        fp = unary_union([r.shape for r in floors[0].rooms]).bounds
        notes.append("plot size unknown; taken from the brief by the caller")
        plot = {"width": round(fp[2], 3), "depth": round(fp[3], 3)}
    return Layout(name=name, source={"kind": "IFC from the baseline (arm A)"}, plot=plot, floors=floors), notes
