"""Hand-drawn reference plans for typical Indian plots (Phase 1): 30x40 ft G+1, 30x50 ft G+1, 30x50 ft G+2.

ResPlan has no plot sizes or orientation, so these give the evaluation a local reference: the layout an
Indian designer would draw for a narrow plot in Guwahati (1.8 m side setbacks leave 5.54 m of width):
living room across the front with the main door, kitchen and dog-leg stair behind it, bedrooms above,
a passage to the bath on the upper floors. Drawn for STRATA, not copied from any published plan.
The 40x60 ft G+1 reference is src/spatial/sample_layout.py.

Rooms are given as rectangles on a structural grid; walls, doors (at the middle of the shared wall),
windows (on the external walls, clear of columns, sized to the opening ratio) and columns (at grid
points that are room corners) are derived, so each plan is a few lines of data. Every plan passes
the rule checker (tests/test_reference_plans.py).
"""

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from src.brief.schema import Brief, Family, Plot, Programme, Road, Site
from src.spatial.layout import Column, Door, Floor, Layout, Materials, Room, Stair, Window, derive_walls

FT = 0.3048
ORIGIN = (1.8, 3.6)  # front-left corner of the buildable envelope: front setback 3.6 m, sides 1.8 m
STAIR = Stair(flight_width=1.0, riser=0.1667, tread=0.25, risers_per_flight=9, headroom=2.2)
W = 5.544  # envelope width on a 30 ft plot
M = 2.8  # middle grid line: bedroom and kitchen bay | stair bay
XS = (0.0, M, W)
OPENING = {"living": 1 / 6, "bedroom": 1 / 6, "dining": 1 / 6, "study": 1 / 6, "kitchen": 1 / 6}
WIN_H, COL_CLEAR, END = 1.2, 0.35, 0.2


def _p(x, y):
    return (round(ORIGIN[0] + x, 4), round(ORIGIN[1] + y, 4))


def _floor(level, name, rooms, doors, ys, main_x=None):
    """rooms: (id, type, x0, y0, x1, y1, label); doors: (a, b) pairs, b may be "outside" (the main door)."""
    rs = [Room(id=r[0], type=r[1], name=r[6] if len(r) > 6 else "",
               polygon=[_p(r[2], r[3]), _p(r[4], r[3]), _p(r[4], r[5]), _p(r[2], r[5])],
               stair=STAIR if r[1] == "stair" else None) for r in rooms]
    by = {r.id: r for r in rs}
    ds = []
    for a, b in doors:
        if b == "outside":
            ds.append(Door(id="D_main", between=(a, "outside"), at=_p(main_x, 0.0), width=1.0, height=2.1, main=True))
            continue
        edge = by[a].shape.boundary.intersection(by[b].shape.boundary)
        line = max((g for g in getattr(edge, "geoms", [edge]) if g.geom_type == "LineString"), key=lambda g: g.length)
        mid = line.interpolate(0.5, normalized=True)
        w = 0.75 if {by[a].type, by[b].type} & {"bath_wc", "store"} else 0.9
        ds.append(Door(id=f"D_{a}_{b}", between=(a, b), at=(round(mid.x, 4), round(mid.y, 4)), width=w))
    on = lambda x, y: any(r[2] - 1e-6 <= x <= r[4] + 1e-6 and r[3] - 1e-6 <= y <= r[5] + 1e-6  # noqa: E731
                          and (abs(x - r[2]) < 1e-6 or abs(x - r[4]) < 1e-6 or abs(y - r[3]) < 1e-6
                               or abs(y - r[5]) < 1e-6) for r in rooms)
    cols = [Column(id=f"C{i}{j}", at=_p(x, y)) for i, x in enumerate(XS) for j, y in enumerate(ys) if on(x, y)]
    ws = _windows(rs, cols, ds)
    f = Floor(level=level, name=name, rooms=rs, doors=ds, windows=ws, columns=cols)
    f.walls = derive_walls(f)
    return f


def _windows(rooms, cols, doors):
    """A window per habitable room and kitchen on its longest free external stretch, a ventilator per bath."""
    outline = unary_union([r.shape for r in rooms]).boundary
    out = []
    for r in rooms:
        ratio = OPENING.get(r.type)
        if not ratio and r.type != "bath_wc":
            continue
        ext = r.shape.boundary.intersection(outline)
        spans = []
        for g in getattr(ext, "geoms", [ext]):
            if g.geom_type != "LineString" or g.length < 0.6:
                continue
            (x0, y0), (x1, y1) = g.coords[0], g.coords[-1]
            horiz = abs(y1 - y0) < 1e-6
            a, b = sorted((x0, x1) if horiz else (y0, y1))
            v = y0 if horiz else x0
            blocked = [(a, a + END), (b - END, b)]
            blocked += [((c.at[0] if horiz else c.at[1]) - COL_CLEAR, (c.at[0] if horiz else c.at[1]) + COL_CLEAR)
                        for c in cols if abs((c.at[1] if horiz else c.at[0]) - v) < 1e-6]
            blocked += [((d.at[0] if horiz else d.at[1]) - 0.7, (d.at[0] if horiz else d.at[1]) + 0.7)
                        for d in doors if d.main and abs((d.at[1] if horiz else d.at[0]) - v) < 1e-6]
            at = a
            for lo, hi in sorted(blocked):
                if lo >= b:
                    break
                if lo - at >= 0.6:
                    spans.append((horiz, v, at, min(lo, b)))
                at = max(at, hi)
            if b - at >= 0.6:
                spans.append((horiz, v, at, b))
        if not spans:
            continue
        spans.sort(key=lambda s: s[2] - s[3])
        if r.type == "bath_wc":
            horiz, v, a, b = spans[0]
            m = (a + b) / 2
            out.append(Window(id=f"V_{r.id}", room=r.id, at=(m, v) if horiz else (v, m), width=0.6, height=0.6,
                              sill=1.8, kind="ventilator"))
            continue
        need = ratio * r.shape.area * 1.1
        for k, (horiz, v, a, b) in enumerate(spans):
            if need <= 0:
                break
            w = round(min(max(0.9, need / WIN_H), b - a), 2)
            m = (a + b) / 2
            out.append(Window(id=f"W_{r.id}_{k}", room=r.id, at=(round(m, 4), round(v, 4)) if horiz
                              else (round(v, 4), round(m, 4)), width=w, height=WIN_H))
            need -= w * WIN_H
    return out


def _brief(depth_ft, floors, bedrooms, attached, common, study=False):
    return Brief(building_use="single_family", floors_now=floors, floors_future=floors, family=Family(persons=5),
                 programme=Programme(bedrooms=bedrooms, bathrooms_attached=attached, bathrooms_common=common,
                                     study=study, dining=False),  # the living room is the living-dining room
                 site=Site(plot=Plot(width=30 * FT, depth=depth_ft * FT), north="E",
                           roads=[Road(side="front", width=6.0)], city="Guwahati", in_ulb=True),
                 sources={k: "client" for k in ("building_use", "floors_now", "floors_future", "family", "programme",
                                                "site.plot", "site.north", "site.roads", "site.city")})


def _layout(name, depth_ft, floors):
    return Layout(name=name, source={"kind": "hand-drawn reference"},
                  plot={"width": round(30 * FT, 4), "depth": round(depth_ft * FT, 4)}, floors=floors,
                  plinth_height=0.45, parapet_height=1.0,
                  materials=Materials(concrete_grade=25, steel_proof_stress=500, steel_elongation=14.5,
                                      masonry="burnt clay brick"))


def _upper(level, name, ys, d1, bed_a, bed_b):
    """Upper floor over the front two bays: two bedrooms at the front, passage, common bath and the stair."""
    y1 = ys[1]
    rooms = [(bed_a, "bedroom", 0.0, 0.0, M, y1), (bed_b, "bedroom", M, 0.0, W, y1),
             ("passage", "corridor", 0.0, y1, M, y1 + 1.6, "Passage"),
             (f"bath{level}", "bath_wc", 0.0, y1 + 1.6, M, d1, "Common bath"),
             ("stair", "stair", M, y1, W, d1)]
    doors = [("passage", "stair"), (bed_a, "passage"), (bed_b, "stair"), (f"bath{level}", "passage")]
    return _floor(level, name, rooms, doors, [y for y in ys if y <= d1 + 1e-6])


def plan_30x40_g1():
    """30x40 ft, G+1, 2 bedrooms: living, kitchen and stair below; two bedrooms and a common bath above."""
    ys = (0.0, 3.4, 6.792)
    ground = _floor(0, "Ground floor", [("living", "living", 0.0, 0.0, W, ys[1], "Living room"),
                                        ("kitchen", "kitchen", 0.0, ys[1], M, 6.792, "Kitchen"),
                                        ("stair", "stair", M, ys[1], W, 6.792)],
                    [("living", "outside"), ("living", "kitchen"), ("living", "stair")], ys, main_x=4.044)
    return _brief(40, 2, 2, 0, 1), _layout("ref_30x40_g1", 40, [ground, _upper(1, "First floor", ys, 6.792,
                                                                               "bed2", "bed1")])


def _ground_30x50(ys):
    d1, d2 = ys[2], ys[3]
    return _floor(0, "Ground floor", [("living", "living", 0.0, 0.0, W, ys[1], "Living room"),
                                      ("kitchen", "kitchen", 0.0, ys[1], M, d1, "Kitchen"),
                                      ("stair", "stair", M, ys[1], W, d1),
                                      ("bath0", "bath_wc", 0.0, d1, 1.5, d2, "Bath of bedroom 1"),
                                      ("bed1", "bedroom", 1.5, d1, W, d2, "Bedroom 1")],
                  [("living", "outside"), ("living", "kitchen"), ("living", "stair"), ("bed1", "stair"),
                   ("bath0", "bed1")], ys, main_x=4.044)


def plan_30x50_g1():
    """30x50 ft, G+1, 3 bedrooms: a ground-floor bedroom with its bath at the rear; two bedrooms above."""
    ys = (0.0, 3.4, 6.7, 9.84)
    return _brief(50, 2, 3, 1, 1), _layout("ref_30x50_g1", 50, [_ground_30x50(ys),
                                                                 _upper(1, "First floor", ys, 6.7, "bed3", "bed2")])


def plan_30x50_g2():
    """30x50 ft, G+2, 5 bedrooms: as the G+1 plan with a second floor of two bedrooms and a common bath."""
    ys = (0.0, 3.4, 6.7, 9.84)
    floors = [_ground_30x50(ys), _upper(1, "First floor", ys, 6.7, "bed3", "bed2"),
              _upper(2, "Second floor", ys, 6.7, "bed5", "bed4")]
    return _brief(50, 3, 5, 1, 2), _layout("ref_30x50_g2", 50, floors)


PLANS = {"30x40_g1": plan_30x40_g1, "30x50_g1": plan_30x50_g1, "30x50_g2": plan_30x50_g2}
