"""Layout generation with a genetic algorithm (DEAP), under hard constraints, no LLM.

Plan grammar, "bands and spine": each floor is cut front to back into 3 or 4 bands. A
1.2 m passage (the spine) runs from the front band line to the rear, and each band holds
one room cell on each side of it. On the ground floor the living room spans the front band
over the spine mouth and holds the main door. This is the usual plan of narrow Indian plots
and gives a regular structural grid: x lines {0, spine, spine + 1.2, width}, y lines at the
band edges. On plots too narrow for two rooms and a spine (under 6.4 m) the spine runs
along the right side with one room per band ("side spine"). Columns stand on every grid crossing, identical on all floors, so upper columns
always sit on lower ones; the spine and the stair cell are shared by all floors, so the
stair lines up by construction.

Genome: floats in [0, 1] (random keys), decoded deterministically:
  [0] footprint width share   [1] band count (3 to 5)   [2..6] band depth shares
  [7] spine position          [8] stair cell              [9] upper floors fill from front or rear
  then for every room except the living room: floor key (ignored if its floor is fixed), order key
The decoder completes the plan: attached baths carved from their bedroom's cell, two small
rooms (bath, store) sharing a cell, doors to the spine, windows sized to the opening ratio,
columns, walls. Fitness is computed here from the geometry (hard terms first, weighted
heavily, then soft ones); the independent rule checker (checker.py) verifies the results.
"""

import math
import random
from dataclasses import dataclass, field

from deap import base, creator, tools

from src.spatial.layout import Column, Door, Floor, Layout, Materials, Room, Stair, Window, derive_walls

SPINE = 1.2  # m, passage width at wall centrelines: 1.085 m clear with half-brick walls
SIDE_SPINE = 1.35  # m, a passage along the outer wall loses a full brick on one side: 1.06 m clear
EXT, INT = 0.23, 0.0575  # wall allowance at an external edge / one side of an internal wall
MIN_CELL, MAX_SPAN = 2.6, 5.0  # m: smallest cell side; largest column spacing (practice)
# With a side spine rooms run across the plot (about 4 m long), so a band can be shallower: a 2.3 m
# band still gives a bedroom 2.19 m clear (NBC: 2.1 m) and holds a dog-leg stair across it.
MIN_ROW = 2.3
BATH_STRIP = 1.75  # m: attached bath carved across the rear of a bedroom cell
BATH_SIDE = 1.6  # m: or down the outer side of the cell when the band is too shallow for a rear strip
BED_MIN = 2.4  # m: bedroom side left after carving (2.1 m clear plus walls, rounded up)
FLOOR_TO_FLOOR = 3.0
WINDOW_H = 1.2
COMFORT = {"living": 16.0, "bedroom": 11.0, "dining": 9.0, "study": 8.0, "kitchen": 7.5, "bath_wc": 3.5,
           "store": 4.0}
SMALL = {"bath_wc", "store"}
HEAD = 10  # genes before the per-room genes
HARD = 1000.0


@dataclass
class Unit:
    """A room (or a bedroom with its attached bath) to place in a cell."""
    name: str
    type: str
    floor: int | None = None  # fixed floor, or None if the GA chooses
    attached: bool = False


@dataclass
class Brief:
    floors: int
    units: list[Unit]
    envelope: tuple[float, float, float, float]  # x0, y0, width, depth in plot coordinates
    plot: tuple[float, float]
    req: dict  # (layout type, quantity) -> minimum, from the spec
    principal: dict
    far: float | None
    opening: dict  # layout type -> opening ratio
    ground_bedrooms: int = 1  # practice: one bedroom downstairs (guests, parents); more if elderly live there
    max_span: float = MAX_SPAN  # lowered when the structural design sends a layout back (see src/structure)


def programme(brief, spec):
    """Units to place, from the spec's room list (which follows the brief's programme)."""
    units, attached = [], sum(1 for r in spec["rooms"] if r["name"].startswith("Attached bathroom"))
    for r in spec["rooms"]:
        name = r["name"]
        if name.startswith("Living"):
            units.append(Unit(name, "living", floor=0))
        elif name.startswith("Bedroom"):
            units.append(Unit(name, "bedroom", attached=attached > 0))
            attached -= 1
        elif name == "Dining":
            units.append(Unit(name, "dining", floor=0))
        elif name == "Kitchen":
            units.append(Unit(name, "kitchen", floor=0))
        elif name.startswith("Study"):
            units.append(Unit(name, "study"))
        elif name.startswith("Common bathroom"):
            units.append(Unit(name, "bath_wc"))
        elif name == "Store":
            units.append(Unit(name, "store"))
    return units


def problem(brief, spec, max_span=MAX_SPAN):
    env = next(c["actual"] for c in spec["checks"] if c["check"].startswith("buildable envelope"))
    s = env["setbacks"]
    plot = brief.site.plot
    req, principal, opening = {}, {}, {}
    kind = {"habitable_room": ("bedroom", "dining", "study"), "kitchen": ("kitchen",), "bath_wc": ("bath_wc",),
            "store": ("store",)}
    for r in spec["rooms"]:
        for q in r["requirements"]:
            if q["comparator"] != "ge" or not isinstance(q["value"], (int, float)):
                continue
            if r.get("room_role") == "principal":
                principal[q["quantity"]] = q["value"]
                if q["quantity"] == "opening_ratio":
                    opening["living"] = q["value"]
                continue
            for t in kind.get(r["type"], ()):
                if q["quantity"] == "opening_ratio":
                    opening[t] = q["value"]
                else:
                    req[(t, q["quantity"])] = max(req.get((t, q["quantity"]), 0), q["value"])
    far = next((e["value"] for e in spec["plot"] if e["quantity"] == "far"), None)
    elderly = brief.family.elderly if brief.family else 0
    beds = brief.programme.bedrooms if brief.programme else 0
    ground = min(beds, max(1, math.ceil(elderly / 2))) if brief.floors_now > 1 else beds
    return Brief(floors=brief.floors_now, units=programme(brief, spec), ground_bedrooms=ground,
                 envelope=(s["left"], s["front"], env["width"], env["depth"]), plot=(plot.width, plot.depth),
                 req=req, principal=principal, far=far, opening=opening, max_span=max_span)


def placed(p):
    """Rooms the genome places (all but the living room, which is the ground front cell)."""
    return [u for u in p.units if u.type != "living"]


def genome_length(p):
    return HEAD + 2 * len(placed(p))


# ---- decoding ----

@dataclass
class Cell:
    band: int
    side: str  # "L", "R", or "F" (ground front band beside the living room)
    x0: float
    x1: float
    y0: float
    y1: float
    rooms: list = field(default_factory=list)  # (name, type, x0, y0, x1, y1)


@dataclass
class Plan:
    width: float
    bands: list  # band edges, y from 0
    spine: float  # x of the spine's left edge
    stair: tuple  # (band, side)
    floors: list  # per floor: list of Cell (the stair cell included, rooms=[("Stair", "stair", ...)])
    extent: list  # per floor: (front, rear) of the built part, y from the envelope front
    overflow: int = 0
    notes: list = field(default_factory=list)
    single: bool = False
    spine_w: float = SPINE


def decode(g, p):
    W = p.envelope[2] * (0.85 + 0.15 * g[0])
    single = W < 2 * MIN_CELL + SPINE  # side spine: one room per band
    least = MIN_ROW if single else MIN_CELL
    n_max = max(3, min(5 if single else 4, int(p.envelope[3] / least)))
    n = 3 + min(int(g[1] * (n_max - 2)), n_max - 3)
    shares = [0.2 + g[2 + k] for k in range(n)]
    D = min(p.envelope[3], p.max_span * n)
    depths = [max(least, min(p.max_span, D * s / sum(shares))) for s in shares]
    if sum(depths) > p.envelope[3]:  # repair: shrink to the envelope, keeping the minimum band
        excess = sum(depths) - p.envelope[3]
        room = [d - least for d in depths]
        depths = [d - excess * r / sum(room) if sum(room) > 0 else d for d, r in zip(depths, room)]
    edges = [0.0]
    for d in depths:
        edges.append(round(edges[-1] + d, 3))
    lo, hi = MIN_CELL, W - SPINE - MIN_CELL
    sw = SIDE_SPINE if single else SPINE
    if single:
        sx = round(W - sw, 3)
    else:
        sx = round(lo + (hi - lo) * g[7], 3)
    stair_slots = [(b, s) for b in range(1, n) for s in ("L" if single else "LR")]
    stair = stair_slots[min(int(g[8] * len(stair_slots)), len(stair_slots) - 1)] if p.floors > 1 else None

    # each room's floor (fixed, or from its gene), then its place in the fill order
    per_floor = {f: [] for f in range(p.floors)}
    for i, u in enumerate(placed(p)):
        f = u.floor if u.floor is not None else min(int(g[HEAD + 2 * i] * p.floors), p.floors - 1)
        per_floor[f].append((g[HEAD + 2 * i + 1], u))

    plan = Plan(width=round(W, 3), bands=edges, spine=sx, stair=stair, floors=[], extent=[], single=single, spine_w=sw)
    rear_first = g[9] >= 0.5  # upper floors: the unbuilt end (terrace) is at the front instead of the rear

    def cells_of(floor):
        cells = []
        for b in range(n):
            y0, y1 = edges[b], edges[b + 1]
            if floor == 0 and b == 0:
                if not single:
                    cells.append(Cell(0, "F", sx + sw, W, y0, y1))
                continue
            cells.append(Cell(b, "L", 0.0, sx, y0, y1))
            if not single:
                cells.append(Cell(b, "R", sx + sw, W, y0, y1))
        return cells

    carry = []
    for f in range(p.floors):
        cells = [c for c in cells_of(f) if (c.band, c.side) != stair]
        if f > 0 and rear_first:
            cells.sort(key=lambda c: (-c.band, c.side))
        stair_cell = next((c for c in cells_of(f) if (c.band, c.side) == stair), None)
        queue = carry + [u for _, u in sorted(per_floor[f], key=lambda t: t[0])]
        # small rooms pair up two to a cell
        groups, small = [], []
        for u in queue:
            if u.type in SMALL and not u.attached:
                small.append(u)
                if len(small) == 2:
                    groups.append(small)
                    small = []
            else:
                groups.append([u])
        if small:
            groups.append(small)
        # ground floor: kitchen and dining wherever the order puts them; cells fill front to rear
        carry = []
        used = []
        for grp, cell in zip(groups, cells):
            _fill(cell, grp)
            used.append(cell)
        for grp in groups[len(cells):]:
            carry += grp
        if stair_cell is not None:
            stair_cell.rooms.append(("Stair", "stair", stair_cell.x0, stair_cell.y0, stair_cell.x1, stair_cell.y1))
            used.append(stair_cell)
        if f == 0:  # living room over the front band, left bay plus the spine mouth; it holds the main door
            liv = Cell(0, "LIV", 0.0, sx + sw, edges[0], edges[1])
            liv.rooms.append(("Living room", "living", liv.x0, liv.y0, liv.x1, liv.y1))
            used.insert(0, liv)
        if f == 0:  # every ground cell is built: unused ones become utility rooms
            for c in cells:
                if not c.rooms:
                    c.rooms.append(("Utility", "utility", c.x0, c.y0, c.x1, c.y1))
                    used.append(c)
        else:  # upper floors: built between the first and last band in use; the rest is terrace
            first = min((c.band for c in used), default=0)
            last = max((c.band for c in used), default=0)
            for c in cells:
                if not c.rooms and first <= c.band <= last:
                    c.rooms.append(("Utility", "utility", c.x0, c.y0, c.x1, c.y1))
                    used.append(c)
        plan.floors.append(used)
        if used:
            plan.extent.append((edges[min(c.band for c in used)], edges[max(c.band for c in used) + 1]))
        else:
            plan.extent.append((edges[0], edges[1]))
    plan.overflow = len(carry)
    return plan


def _fill(cell, grp):
    x0, y0, x1, y1 = cell.x0, cell.y0, cell.x1, cell.y1
    if len(grp) == 1:
        u = grp[0]
        side_fits = (x1 - x0) >= BATH_SIDE + 2.1 + 2 * INT + 0.02  # bedroom keeps 2.1 m clear
        if u.attached and ((y1 - y0) >= BATH_STRIP + BED_MIN or not side_fits):
            cut = round(y1 - BATH_STRIP, 3)  # bath across the rear of the cell, on the outer wall
            cell.rooms.append((u.name, "bedroom", x0, y0, x1, cut))
            cell.rooms.append((f"Bath of {u.name}", "bath_wc", x0, cut, x1, y1))
        elif u.attached:  # bath down the outer side (external wall); the bedroom keeps the spine side
            if cell.side == "R":
                cut = round(x1 - BATH_SIDE, 3)
                cell.rooms.append((u.name, "bedroom", x0, y0, cut, y1))
                cell.rooms.append((f"Bath of {u.name}", "bath_wc", cut, y0, x1, y1))
            else:
                cut = round(x0 + BATH_SIDE, 3)
                cell.rooms.append((u.name, "bedroom", cut, y0, x1, y1))
                cell.rooms.append((f"Bath of {u.name}", "bath_wc", x0, y0, cut, y1))
        elif u.type in SMALL:  # a lone small room keeps the front half; the rest is utility
            mid = round((y0 + y1) / 2, 3)
            cell.rooms.append((u.name, u.type, x0, y0, x1, mid))
            cell.rooms.append(("Utility", "utility", x0, mid, x1, y1))
        else:
            cell.rooms.append((u.name, u.type, x0, y0, x1, y1))
    else:  # two small rooms, one behind the other; both reach the spine and the outer wall
        mid = round((y0 + y1) / 2, 3)
        cell.rooms.append((grp[0].name, grp[0].type, x0, y0, x1, mid))
        cell.rooms.append((grp[1].name, grp[1].type, x0, mid, x1, y1))


# ---- fitness ----

def clear(room, plan, floor):
    """Clear width and depth inside the walls (external edges lose a full brick, internal half)."""
    _, _, x0, y0, x1, y1 = room
    W, (F, D) = plan.width, plan.extent[floor]
    ax = (EXT if x0 <= 1e-6 else INT) + (EXT if x1 >= W - 1e-6 else INT)
    ay = (EXT if y0 <= F + 1e-6 else INT) + (EXT if y1 >= D - 1e-6 else INT)
    return x1 - x0 - ax, y1 - y0 - ay


def external_edge(room, plan, floor):
    _, _, x0, y0, x1, y1 = room
    W, (F, D) = plan.width, plan.extent[floor]
    edges = []
    if x0 <= 1e-6:
        edges.append(("x", 0.0, y0, y1))
    if x1 >= W - 1e-6:
        edges.append(("x", W, y0, y1))
    if y0 <= F + 1e-6:
        edges.append(("y", F, x0, x1))
    if y1 >= D - 1e-6:
        edges.append(("y", D, x0, x1))
    return edges


def stair_design(f2f=FLOOR_TO_FLOOR, riser_max=0.19):
    n = math.ceil(f2f / riser_max)
    n += n % 2
    return Stair(flight_width=1.0, riser=round(f2f / n, 4), tread=0.25, risers_per_flight=n // 2, headroom=2.2)


def evaluate(g, p, detail=False):
    plan = decode(g, p)
    hard, soft, why = 0.0, 0.0, []

    def bad(amount, reason):
        nonlocal hard
        hard += amount
        if detail:
            why.append(reason)

    if plan.overflow:
        bad(HARD * plan.overflow, f"{plan.overflow} rooms did not fit")
    W = plan.width
    for span, label in ((plan.spine, "left bay"), (W - plan.spine - plan.spine_w, "right bay")) if not plan.single else (
            (plan.spine, "bay"),):
        if span > p.max_span + 1e-6:
            bad(HARD * (span - p.max_span), f"{label} span {span:.2f} m")
    st = stair_design()
    wet_below, wet_above = set(), set()
    built = 0.0
    for f, cells in enumerate(plan.floors):
        built += W * (plan.extent[f][1] - plan.extent[f][0])
        for cell in cells:
            for room in cell.rooms:
                name, t, x0, y0, x1, y1 = room
                cw, cd = clear(room, plan, f)
                area, width = cw * cd, min(cw, cd)
                if t == "stair":
                    short, long_ = sorted((cw, cd))
                    going = (st.risers_per_flight - 1) * st.tread + st.flight_width
                    if short < 2 * st.flight_width:
                        bad(HARD * (2 * st.flight_width - short), f"stair cell {short:.2f} m wide")
                    if long_ < going:
                        bad(HARD * (going - long_), f"stair cell {long_:.2f} m long")
                    soft += 0.15 * max(0.0, cw * cd - (2 * st.flight_width + 0.1) * going)  # stair space beyond need
                    continue
                if t == "living":
                    need_a, need_w = p.principal.get("area", 9.5), p.principal.get("width", 2.4)
                else:
                    need_a, need_w = p.req.get((t, "area"), 0), p.req.get((t, "width"), 0)
                if area < need_a:
                    bad(HARD * (need_a - area), f"{name} {area:.1f} m2 < {need_a}")
                if width < need_w:
                    bad(HARD * (need_w - width), f"{name} {width:.2f} m wide < {need_w}")
                ext = external_edge(room, plan, f)
                if t == "bath_wc" and not ext:
                    bad(HARD, f"{name} has no external wall")
                ratio = p.opening.get(t)
                if ratio:
                    capacity = sum(max(0.0, (e[3] - e[2]) - 0.4) * WINDOW_H for e in ext)
                    if capacity < ratio * area:
                        bad(HARD * (ratio * area - capacity), f"{name} cannot get enough window")
                if t in COMFORT:
                    soft += ((area - COMFORT[t]) / COMFORT[t]) ** 2
                if t not in ("utility",):
                    long_, short = max(cw, cd), max(min(cw, cd), 0.1)
                    soft += 0.3 * max(0.0, long_ / short - 1.8)
                if t in ("bath_wc", "kitchen"):
                    (wet_below if f == 0 else wet_above).add((cell.band, cell.side))
                if t == "utility":
                    soft += 0.1 * (x1 - x0) * (y1 - y0)
    if p.far and built / (p.plot[0] * p.plot[1]) > p.far + 1e-6:
        bad(HARD * (built / (p.plot[0] * p.plot[1]) - p.far) * 10, "FAR exceeded")
    # a bedroom without its own bath needs a common bath on the same floor (no stairs at night)
    for cells in plan.floors:
        rooms = [r for c in cells for r in c.rooms]
        attached = {r[0][len("Bath of "):] for r in rooms if r[0].startswith("Bath of ")}
        lonely = [r for r in rooms if r[1] == "bedroom" and r[0] not in attached]
        common = [r for r in rooms if r[1] == "bath_wc" and not r[0].startswith("Bath of ")]
        if lonely and not common:
            soft += 2.0
    ground_beds = sum(1 for c in plan.floors[0] for r in c.rooms if r[1] == "bedroom")
    soft += 0.5 * abs(ground_beds - p.ground_bedrooms)
    # baths upstairs over wet rooms below share the plumbing stack
    soft += 0.5 * len(wet_above - wet_below)
    # kitchen next to dining (same side, adjacent bands): 96% of library plans join them
    g0 = {r[1]: c for c in plan.floors[0] for r in c.rooms}
    k, d = g0.get("kitchen"), g0.get("dining")
    if k and d and not (k.side == d.side and abs(k.band - d.band) == 1):
        soft += 1.0
    if detail:
        return hard, soft, why, plan
    return (hard + soft,)


# ---- the GA ----

def run(brief, spec, pop_size=150, generations=150, seed=0, seeds=(), restarts=1, max_span=MAX_SPAN):
    """Best of `restarts` independent runs (each cheap); see `_run`."""
    best = None
    for k in range(restarts):
        hof, p, history = _run(brief, spec, pop_size, generations, seed + 1000 * k, seeds, max_span)
        if best is None or hof[0].fitness.values[0] < best[0][0].fitness.values[0]:
            best = (hof, p, history)
    return best


def alternatives(brief, spec, count=3, restarts=8, seed=0, seeds=(), **kw):
    """Up to `count` distinct layouts, best first: the winner of each independent run, duplicates removed.

    Independent runs settle on different arrangements, which gives the client real choices."""
    found, seen = [], set()
    p = None
    for k in range(restarts):
        hof, p, _ = _run(brief, spec, kw.get("pop_size", 150), kw.get("generations", 150), seed + 1000 * k, seeds,
                         kw.get("max_span", MAX_SPAN))
        for g in hof:
            plan = decode(g, p)
            key = tuple((r[0], round(r[2], 1), round(r[3], 1)) for cells in plan.floors for c in cells for r in c.rooms)
            if key not in seen:
                seen.add(key)
                found.append((g.fitness.values[0], list(g)))
    found.sort(key=lambda t: t[0])
    return [g for _, g in found[:count]], p


def _run(brief, spec, pop_size, generations, seed, seeds, max_span=MAX_SPAN):
    """Evolve layouts; returns (best genomes, problem). `seeds` are genomes to start from
    (e.g. from library plans or LLM proposals)."""
    p = problem(brief, spec, max_span)
    n = genome_length(p)
    if not hasattr(creator, "LayoutFitness"):
        creator.create("LayoutFitness", base.Fitness, weights=(-1.0,))
        creator.create("LayoutGenome", list, fitness=creator.LayoutFitness)
    rng = random.Random(seed)
    random.seed(seed)
    tb = base.Toolbox()
    tb.register("genome", lambda: creator.LayoutGenome([rng.random() for _ in range(n)]))
    tb.register("population", tools.initRepeat, list, tb.genome)
    tb.register("evaluate", evaluate, p=p)
    tb.register("mate", tools.cxSimulatedBinaryBounded, eta=15.0, low=0.0, up=1.0)
    tb.register("mutate", tools.mutPolynomialBounded, eta=10.0, low=0.0, up=1.0, indpb=2.0 / n)
    tb.register("select", tools.selTournament, tournsize=3)
    pop = [creator.LayoutGenome(list(s)) for s in seeds][:pop_size]
    pop += tb.population(n=pop_size - len(pop))
    hof = tools.HallOfFame(10)
    history = []
    for ind in pop:
        ind.fitness.values = tb.evaluate(ind)
    for gen in range(generations):
        offspring = [tb.clone(x) for x in tb.select(pop, len(pop))]
        for a, b in zip(offspring[::2], offspring[1::2]):
            if rng.random() < 0.9:
                tb.mate(a, b)
                del a.fitness.values, b.fitness.values
        for m in offspring:
            if rng.random() < 0.5:
                tb.mutate(m)
                del m.fitness.values
        for ind in offspring:
            if not ind.fitness.valid:
                ind.fitness.values = tb.evaluate(ind)
        elite = tools.selBest(pop, 2)  # keep the two best unchanged
        pop = elite + tools.selBest(offspring, len(pop) - 2)
        hof.update(pop)
        history.append(min(i.fitness.values[0] for i in pop))
    return hof, p, history


# ---- to layout JSON ----

def to_layout(g, p, name="ga_layout"):
    hard, soft, why, plan = evaluate(g, p, detail=True)
    x0, y0 = p.envelope[0], p.envelope[1]
    P = lambda x, y: (round(x0 + x, 4), round(y0 + y, 4))  # noqa: E731
    sx, W, SW = plan.spine, plan.width, plan.spine_w
    floors = []
    st = stair_design()
    for f, cells in enumerate(plan.floors):
        F0, D = plan.extent[f]
        rooms, doors, windows = [], [], []
        ids = {}

        def add(rid, t, a, b, c, d, label=""):
            ids[label or rid] = rid
            rooms.append(Room(id=rid, type=t, name=label, polygon=[P(a, b), P(c, b), P(c, d), P(a, d)],
                              stair=st if t == "stair" else None))

        spine_from = plan.bands[1] if f == 0 else F0
        add("spine", "corridor", sx, spine_from, sx + SW, D, "Passage")
        counter = {}
        for cell in cells:
            for (rname, t, a, b, c, d) in cell.rooms:
                counter[t] = counter.get(t, 0) + 1
                rid = "living" if cell.side == "LIV" else f"{t}{counter[t]}"
                add(rid, t, a, b, c, d, rname)
                room = (rname, t, a, b, c, d)
                if cell.side == "LIV":
                    doors.append(Door(id="D_main", between=("living", "outside"), at=P(sx + SW / 2, 0.0),
                                      width=1.0, height=2.1, main=True))
                    doors.append(Door(id="D_spine", between=("living", "spine"), at=P(sx + SW / 2, d),
                                      width=1.0))
                    _windows(windows, rid, room, plan, f, p.opening.get("living"), t, P, avoid=(sx + SW / 2, 0.0))
                    continue
                is_inner_bath = rname.startswith("Bath of ")
                if is_inner_bath:  # door to its bedroom, on the edge they share
                    owner_name = rname[len("Bath of "):]
                    o = next(r for r in cell.rooms if r[0] == owner_name)
                    if abs(o[5] - b) < 1e-6:  # bedroom in front of the bath
                        at = ((a + c) / 2, b)
                    elif abs(o[2] - c) < 1e-6:  # bedroom to the right
                        at = (c, (b + d) / 2)
                    else:  # bedroom to the left
                        at = (a, (b + d) / 2)
                    doors.append(Door(id=f"D_{rid}", between=(rid, ids[owner_name]), at=P(*at), width=0.75))
                elif cell.side == "F":  # ground front band beside the living room
                    doors.append(Door(id=f"D_{rid}", between=(rid, "living"), at=P(a, (b + d) / 2), width=0.9))
                else:
                    xs = c if cell.side == "L" else a
                    w = 0.75 if t in ("bath_wc", "store") else 0.9
                    doors.append(Door(id=f"D_{rid}", between=(rid, "spine"), at=P(xs, (b + d) / 2), width=w))
                _windows(windows, rid, room, plan, f, p.opening.get(t), t, P)
        xs = sorted({0.0, sx, round(sx + SW, 3), W})
        ys = [y for y in plan.bands if F0 - 1e-6 <= y <= D + 1e-6]
        columns = [Column(id=f"C{i}{j}", at=P(x, y)) for i, x in enumerate(xs) for j, y in enumerate(ys)]
        fl = Floor(level=f, name=["Ground floor", "First floor", "Second floor"][f], floor_to_floor=FLOOR_TO_FLOOR,
                   rooms=rooms, doors=doors, windows=windows, columns=columns)
        fl.walls = derive_walls(fl)
        floors.append(fl)
    return Layout(name=name, source={"kind": "GA", "fitness_hard": round(hard, 3), "fitness_soft": round(soft, 3),
                                     "genome": [round(v, 5) for v in g], "violations": why},
                  plot={"width": round(p.plot[0], 4), "depth": round(p.plot[1], 4)}, floors=floors,
                  plinth_height=0.45, parapet_height=1.0,
                  materials=Materials(concrete_grade=25, steel_proof_stress=500, steel_elongation=14.5,
                                      masonry="burnt clay brick"))


def _windows(out, rid, room, plan, f, ratio, t, P, avoid=None):
    if t == "bath_wc":
        ext = external_edge(room, plan, f)
        if ext:
            axis, v, a, b = max(ext, key=lambda e: e[3] - e[2])
            m = (a + b) / 2
            out.append(Window(id=f"V_{rid}", room=rid, at=P(v, m) if axis == "x" else P(m, v), width=0.6, height=0.6,
                              sill=1.8, kind="ventilator"))
        return
    if not ratio:
        return
    cw, cd = clear(room, plan, f)
    need = ratio * cw * cd * 1.05
    for k, (axis, v, a, b) in enumerate(sorted(external_edge(room, plan, f), key=lambda e: e[2] - e[3])):
        if need <= 0:
            break
        length = b - a
        centre = (a + b) / 2
        if avoid and axis == "y" and abs(v - avoid[1]) < 1e-6:  # keep clear of the main door
            usable = max(a, avoid[0] + 0.7), b
            if usable[1] - usable[0] < 1.0:
                usable = a, min(b, avoid[0] - 0.7)
            a, b = usable
            length, centre = b - a, (a + b) / 2
        w = round(min(max(0.9, need / WINDOW_H), length - 0.4), 2)
        if w < 0.6:
            continue
        out.append(Window(id=f"W_{rid}_{k}", room=rid, at=P(v, centre) if axis == "x" else P(centre, v), width=w,
                          height=WINDOW_H))
        need -= w * WINDOW_H


# ---- when nothing fits: which brief changes would ----

def _variants(brief):
    """Plain-language brief changes a designer would offer, fewest changes first."""
    p = brief.programme
    out = []
    if brief.floors_now < 3:
        out.append(("one more storey", {"floors_now": brief.floors_now + 1,
                                        "floors_future": max(brief.floors_future or 0, brief.floors_now + 1)}, {}))
    if p.kitchen != "with_dining":
        out.append(("kitchen with dining (no separate dining room)", {}, {"kitchen": "with_dining"}))
    if p.bathrooms_attached > 0:
        out.append(("one attached bath becomes a common bath", {},
                    {"bathrooms_attached": p.bathrooms_attached - 1, "bathrooms_common": p.bathrooms_common + 1}))
    singles = list(out)
    for i, a in enumerate(singles):
        for b in singles[i + 1:]:
            out.append((f"{a[0]} and {b[0]}", {**a[1], **b[1]}, {**a[2], **b[2]}))
    return out


def suggest(brief, build_spec, restarts=3, generations=100, seed=0):
    """Try each variant of the brief with the GA; report which fit (no hard violation) and how well."""
    results = []
    for label, top, prog in _variants(brief):
        b = brief.model_copy(deep=True, update=top)
        if prog:
            b.programme = b.programme.model_copy(update=prog)
        spec = build_spec(b)
        hof, p, _ = run(b, spec, generations=generations, seed=seed, restarts=restarts)
        hard, soft, why, _ = evaluate(hof[0], p, detail=True)
        results.append({"change": label, "fits": hard == 0, "score": round(hard + soft, 3), "problems": why,
                        "brief": b})
    results.sort(key=lambda r: (not r["fits"], r["change"].count(" and "), r["score"]))
    return results
