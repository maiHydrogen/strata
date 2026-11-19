"""Courtyard plan type for the layout GA ("chowk" house): rooms around a central court open to the sky.

Used when the client asks for a central courtyard or open verandah, or when the plot is too wide for
rooms either side of one passage (each side would span more than the beam limit). Same genome as the
passage plan (ga.py), read differently:

  [0] footprint width share      [1] bay band count (2 to 4)     [2..5] bay band depth shares
  [6] front row depth            [7] side bay width              [8] stair cell
  [9] rear row: none below 0.5, else its depth (2.7 to 4.0 m)
  then per room: floor key, order key (as in ga.py)

Plan, front (road) at y = 0:

    +------+--------------------------+------+
    |  FL  |   living (ground floor)  |  FR  |   front row, depth yf
    +------+---+------------------+---+------+
    |  L1  | v |      verandah    | v |  R1  |
    +------+ e +------------------+ e +------+   side bays, width L, bands front to rear
    |  L2  | r |   COURT (open)   | r |  R2  |
    +------+ a +------------------+ a +------+
    |  L3  |   |      verandah    |   |  R3  |
    |      +---+------------------+---+      |
    |      |      RM (rear row)       |      |   optional
    +------+--------------------------+------+

The court is a hole in the floor plate (no room covers it), so the walls round it are external and
the frame puts no slab over it. A 1.5 m covered verandah rings it as four open corridor strips with no
walls between them or towards the court (posts only). Every room opens onto the verandah, or onto the
living room for the two front corners. Grid lines: x {0, L, L+V, W-L-V, W-L, W}, y at the row and band
edges, so spans are L, the bay bands, yf, and the court width (over the living room and the rear row).
The side bays are equal (a symmetric frame); the rooms in them are placed by the genome, and the
symmetric wish scores matching room kinds across the court.
"""

from __future__ import annotations

from src.spatial import ga as G
from src.spatial.layout import Column, Door, Floor, Room, Window, derive_walls

VERANDAH = 1.5  # m: covered verandah round the court (NBC corridor minimum 1.0 m; practice for a verandah)
FRONT_MIN = 2.7  # m, front and rear row depth range
REAR_MAX = 4.0
DOOR_REACH = 1.2  # m of a room's wall that must face the verandah for a door with its frame
FRONT_SIDES = ("FL", "FR")


def side_of(cell):
    """L or R for the side bays and front corners (for kitchen-dining adjacency and symmetry)."""
    return {"FL": "L", "FR": "R"}.get(cell.side, cell.side)


def decode(g, p):
    W = p.envelope[2] * (0.85 + 0.15 * g[0])
    Dmax = p.envelope[3]
    V = VERANDAH
    L = round(G.MIN_CELL + (p.max_span - G.MIN_CELL) * g[7], 3)
    L = round(max(G.MIN_CELL, min(L, (W - 2 * V - p.court_min) / 2)), 3)  # leave the court its minimum width
    top_f = max(FRONT_MIN, min(p.max_span, Dmax / 3))
    yf = round(FRONT_MIN + (top_f - FRONT_MIN) * g[6], 3)
    rd = round(FRONT_MIN + (REAR_MAX - FRONT_MIN) * (g[9] - 0.5) * 2, 3) if g[9] >= 0.5 else 0.0
    nb = 2 + min(int(g[1] * 3), 2)
    shares = [0.2 + g[2 + k] for k in range(nb)]
    room_ = Dmax - yf
    D = min(room_, nb * p.max_span)
    depths = [max(G.MIN_CELL, min(p.max_span, D * s / sum(shares))) for s in shares]
    if sum(depths) > room_:
        excess = sum(depths) - room_
        spare = [d - G.MIN_CELL for d in depths]
        depths = [d - excess * r / sum(spare) if sum(spare) > 0 else d for d, r in zip(depths, spare)]
    edges = [yf]
    for d in depths:
        edges.append(round(edges[-1] + d, 3))
    Dd = edges[-1]
    yv = round(Dd - rd, 3)  # the verandah ring and court end here; the rear row is behind

    plan = G.Plan(width=round(W, 3), bands=[0.0] + edges, spine=L, stair=None, floors=[], extent=[], spine_w=V)
    plan.kind = "court"
    plan.court = (round(L + V, 3), round(yf + V, 3), round(W - L - V, 3), round(yv - V, 3))
    plan.yv, plan.yf, plan.rear = yv, yf, rd
    plan.verandahs = [("left", L, yf, L + V, yv), ("right", W - L - V, yf, W - L, yv),
                      ("front", L + V, yf, W - L - V, yf + V), ("back", L + V, yv - V, W - L - V, yv)]

    def cells_of(floor):
        cs = [G.Cell(0, "FL", 0.0, L, 0.0, yf), G.Cell(0, "FR", W - L, W, 0.0, yf)]
        for b, (y0, y1) in enumerate(zip(edges, edges[1:]), 1):
            cs += [G.Cell(b, "L", 0.0, L, y0, y1), G.Cell(b, "R", W - L, W, y0, y1)]
        if rd:
            cs.append(G.Cell(nb + 1, "RM", L, W - L, yv, Dd))
        return cs

    need_stair = p.floors > 1 or p.roof_stair
    if need_stair:
        slots = [(c.band, c.side) for c in cells_of(0) if c.side in ("L", "R") or c.side in FRONT_SIDES]
        plan.stair = slots[min(int(g[8] * len(slots)), len(slots) - 1)]

    per_floor = {f: [] for f in range(p.floors)}
    for i, u in enumerate(G.placed(p)):
        f = u.floor if u.floor is not None else min(int(g[G.HEAD + 2 * i] * p.floors), p.floors - 1)
        per_floor[f].append((g[G.HEAD + 2 * i + 1], u))

    carry = []
    for f in range(p.floors):
        cells = [c for c in cells_of(f) if (c.band, c.side) != plan.stair]
        stair_cell = next((c for c in cells_of(f) if (c.band, c.side) == plan.stair), None)
        queue = carry + [u for _, u in sorted(per_floor[f], key=lambda t: t[0])]
        groups, small = [], []
        for u in queue:
            if u.type in G.SMALL and not u.attached:
                small.append(u)
                if len(small) == 2:
                    groups.append(small)
                    small = []
            else:
                groups.append([u])
        if small:
            groups.append(small)
        used = []
        for grp, cell in zip(groups, cells):
            fill(cell, grp)
            used.append(cell)
        carry = [u for grp in groups[len(cells):] for u in grp]
        if stair_cell is not None:
            stair_cell.rooms.append(("Stair", "stair", stair_cell.x0, stair_cell.y0, stair_cell.x1, stair_cell.y1))
            used.append(stair_cell)
        if f == 0:
            liv = G.Cell(0, "LIV", L, W - L, 0.0, yf)
            liv.rooms.append(("Living room", "living", liv.x0, liv.y0, liv.x1, liv.y1))
            used.insert(0, liv)
        for c in cells:  # every cell of the ring is built: unused ones become utility rooms
            if not c.rooms:
                c.rooms.append(("Utility", "utility", c.x0, c.y0, c.x1, c.y1))
                used.append(c)
        plan.floors.append(used)
        plan.extent.append((0.0, Dd))
    plan.overflow = len(carry)
    return plan


def fill(cell, grp):
    """As ga._fill, except that the rear-row cell opens onto the verandah along a long side: two rooms
    there sit side by side, not one behind the other, so both reach it."""
    if cell.side != "RM" or len(grp) == 1:
        G._fill(cell, grp)
        return
    mid = round((cell.x0 + cell.x1) / 2, 3)
    cell.rooms.append((grp[0].name, grp[0].type, cell.x0, cell.y0, mid, cell.y1))
    cell.rooms.append((grp[1].name, grp[1].type, mid, cell.y0, cell.x1, cell.y1))


def _overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def reach(room, cell, plan):
    """Length of the room's wall that faces its circulation (verandah, or the living room for a front corner)."""
    _, _, x0, y0, x1, y1 = room
    cx0, cy0, cx1, cy1 = plan.court
    if cell.side in ("L", "R"):
        on = abs((x1 if cell.side == "L" else x0) - (cx0 - VERANDAH if cell.side == "L" else cx1 + VERANDAH)) < 1e-6
        return _overlap(y0, y1, plan.yf, plan.yv) if on else 0.0
    if cell.side in FRONT_SIDES:
        on = abs((x1 if cell.side == "FL" else x0) - (cx0 - VERANDAH if cell.side == "FL" else cx1 + VERANDAH)) < 1e-6
        return (y1 - y0) if on else 0.0
    if cell.side == "RM":
        return _overlap(x0, x1, cx0, cx1) if abs(y0 - plan.yv) < 1e-6 else 0.0
    return 99.0


def hard_terms(plan, p, bad):
    """Court plan constraints: spans, the court's NBC size, a door to the verandah for every room."""
    W, L, V = plan.width, plan.spine, VERANDAH
    cx0, cy0, cx1, cy1 = plan.court
    cw, cd = cx1 - cx0, cy1 - cy0
    for span, label in ((L, "side bay"), (plan.yf, "front row"), (cw, "court width (span over the living room)"),
                        *((d, "bay band") for d in (b - a for a, b in zip(plan.bands[1:], plan.bands[2:])))):
        if span > p.max_span + 1e-6:
            bad(G.HARD * (span - p.max_span), f"{label} span {span:.2f} m")
    if min(cw, cd) < p.court_min - 1e-6:
        bad(G.HARD * (p.court_min - min(cw, cd)), f"court {min(cw, cd):.2f} m wide < {p.court_min:g} m")
    height = p.floors * G.FLOOR_TO_FLOOR + 1.0  # highest abutting wall: the storeys and the roof parapet
    if cw * cd < (p.court_fraction * height) ** 2 - 1e-6:
        bad(G.HARD, f"court {cw * cd:.1f} m2 < ({p.court_fraction:g} x {height:g} m)^2")
    for f, cells in enumerate(plan.floors):
        for cell in cells:
            if cell.side == "LIV":
                continue
            for room in cell.rooms:
                if room[0].startswith("Bath of "):
                    continue
                r = reach(room, cell, plan)
                if r < DOOR_REACH - 1e-6:
                    bad(G.HARD * (DOOR_REACH - r), f"{room[0]} has {r:.2f} m of wall on the verandah")


COURT_COMFORT = 25.0  # m2: a court beyond about 5 m x 5 m takes area from the rooms (practice)


def soft_terms(plan, p):
    soft = 0.0
    cx0, cy0, cx1, cy1 = plan.court
    soft += 0.04 * max(0.0, (cx1 - cx0) * (cy1 - cy0) - COURT_COMFORT)
    # rooms below a comfortable size: a court plan can always trade court and verandah for room area
    for f, cells in enumerate(plan.floors):
        for c in cells:
            for r in c.rooms:
                if r[1] in G.COMFORT and r[1] != "bath_wc":
                    w, d = G.clear(r, plan, f)
                    soft += 1.5 * max(0.0, G.COMFORT[r[1]] - w * d) / G.COMFORT[r[1]]
    if p.symmetric:
        for f, cells in enumerate(plan.floors):
            kinds = {}
            for c in cells:
                if c.side in ("L", "R", "FL", "FR"):
                    kinds[(c.band, side_of(c))] = tuple(sorted(r[1] for r in c.rooms))
            for (band, side), k in kinds.items():
                if side == "L" and kinds.get((band, "R")) != k:
                    soft += 0.75
    return soft


def to_layout_floor(plan, p, f, cells, P):
    """Rooms, doors and windows of one floor of a court plan (in envelope coordinates through P)."""
    W, L, V = plan.width, plan.spine, VERANDAH
    cx0, cy0, cx1, cy1 = plan.court
    st = G.stair_design()
    rooms, doors, windows, ids = [], [], [], {}

    def add(rid, t, a, b, c, d, label="", open_=False):
        ids[label or rid] = rid
        rooms.append(Room(id=rid, type=t, name=label, polygon=[P(a, b), P(c, b), P(c, d), P(a, d)],
                          stair=st if t == "stair" else None, open=open_))

    for name, a, b, c, d in plan.verandahs:
        add(f"verandah_{name}", "corridor", a, b, c, d, f"Verandah ({name})", open_=True)
    for a_, b_ in (("left", "front"), ("front", "right"), ("right", "back"), ("back", "left")):
        ra = next(v for v in plan.verandahs if v[0] == a_)
        rb = next(v for v in plan.verandahs if v[0] == b_)
        x = cx0 if "left" in (a_, b_) else cx1
        y = (plan.yf + cy0) / 2 if "front" in (a_, b_) else (cy1 + plan.yv) / 2
        doors.append(Door(id=f"O_{a_}_{b_}", between=(f"verandah_{a_}", f"verandah_{b_}"), at=P(x, y),
                          width=round(min(V, (ra[4] - ra[2]) if a_ in ("left", "right") else V) - 0.1, 3), kind="opening"))
    if f > 0:  # upper floors: the front middle is an open lobby joining the front corners to the verandah
        add("lobby", "corridor", L, 0.0, W - L, plan.yf, "Lobby", open_=True)
        doors.append(Door(id="O_lobby", between=("lobby", "verandah_front"), at=P(W / 2, plan.yf), width=round(cx1 - cx0 - 0.2, 3),
                          kind="opening"))
    counter = {}
    for cell in cells:
        for (rname, t, a, b, c, d) in cell.rooms:
            counter[t] = counter.get(t, 0) + 1
            rid = "living" if cell.side == "LIV" else f"{t}{counter[t]}"
            add(rid, t, a, b, c, d, rname)
            room = (rname, t, a, b, c, d)
            if cell.side == "LIV":
                doors.append(Door(id="D_main", between=("living", "outside"), at=P(W / 2, 0.0), width=1.0,
                                  height=2.1, main=True))
                doors.append(Door(id="D_verandah", between=("living", "verandah_front"), at=P(W / 2, d), width=1.2))
                G._windows(windows, rid, room, plan, f, p.opening.get("living"), t, P, avoid=(W / 2, 0.0))
                continue
            w = 0.75 if t in ("bath_wc", "store") else 0.9
            if rname.startswith("Bath of "):
                owner = next(r for r in cell.rooms if r[0] == rname[len("Bath of "):])
                if abs(owner[5] - b) < 1e-6:
                    at = ((a + c) / 2, b)
                elif abs(owner[2] - c) < 1e-6:
                    at = (c, (b + d) / 2)
                else:
                    at = (a, (b + d) / 2)
                doors.append(Door(id=f"D_{rid}", between=(rid, ids[owner[0]]), at=P(*at), width=0.75))
            elif cell.side in FRONT_SIDES:
                x = c if cell.side == "FL" else a
                doors.append(Door(id=f"D_{rid}", between=(rid, "living" if f == 0 else "lobby"),
                                  at=P(x, (b + d) / 2), width=w))
            elif cell.side in ("L", "R"):
                x = c if cell.side == "L" else a
                lo, hi = max(b, plan.yf), min(d, plan.yv)
                doors.append(Door(id=f"D_{rid}", between=(rid, f"verandah_{'left' if cell.side == 'L' else 'right'}"),
                                  at=P(x, (lo + hi) / 2), width=w))
            elif cell.side == "RM":
                lo, hi = max(a, cx0), min(c, cx1)
                doors.append(Door(id=f"D_{rid}", between=(rid, "verandah_back"), at=P((lo + hi) / 2, b), width=w))
            G._windows(windows, rid, room, plan, f, p.opening.get(t), t, P)
    return rooms, doors, windows


def posts(plan):
    """Column centres (y from the envelope front): the side bays, the verandah edges and the court corners."""
    W, L, V = plan.width, plan.spine, VERANDAH
    xs = sorted({0.0, round(L, 3), round(L + V, 3), round(W - L - V, 3), round(W - L, 3), round(W, 3)})
    ys = sorted({round(y, 3) for y in plan.bands} | {round(plan.yf + V, 3), round(plan.yv - V, 3), round(plan.yv, 3)})
    cx0, cy0, cx1, cy1 = plan.court
    return [(x, y) for x in xs for y in ys
            if not (cx0 + 1e-6 < x < cx1 - 1e-6 and cy0 + 1e-6 < y < cy1 - 1e-6)]  # none inside the court


def columns(plan, P):
    pts = posts(plan)
    xs, ys = sorted({x for x, _ in pts}), sorted({y for _, y in pts})
    return [Column(id=f"C{xs.index(x)}{ys.index(y)}", at=P(x, y)) for x, y in pts]


def floor(plan, p, f, cells, P, name):
    rooms, doors, windows = to_layout_floor(plan, p, f, cells, P)
    fl = Floor(level=f, name=name, floor_to_floor=G.FLOOR_TO_FLOOR, rooms=rooms, doors=doors, windows=windows,
               columns=columns(plan, P))
    fl.walls = derive_walls(fl)
    return fl
