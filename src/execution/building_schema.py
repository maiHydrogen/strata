"""Building JSON v0: the single source of truth handed to the Revit builder.

Plain data only, all lengths in metres, plan coordinates (x, y) with z up.
`validate_building` runs the cross-checks in WSL before anything reaches
Revit, so the builder can assume a consistent model.
"""

import math
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "0.4"

Point = tuple[float, float]


class Level(BaseModel):
    id: str
    name: str
    elevation: float


class Layer(BaseModel):
    material: str
    thickness: float = Field(gt=0)
    function: Literal["Structure", "Substrate", "Finish1", "Finish2", "Membrane"] = "Structure"


class WallType(BaseModel):
    id: str
    name: str
    layers: list[Layer] = Field(min_length=1)

    @property
    def thickness(self) -> float:
        return sum(layer.thickness for layer in self.layers)


class Wall(BaseModel):
    id: str
    level: str
    type: str
    start: Point
    end: Point
    height: float = Field(gt=0)
    external: bool = False
    parapet: bool = False  # a parapet on a roof or terrace edge

    @property
    def length(self) -> float:
        return math.dist(self.start, self.end)


class Opening(BaseModel):
    id: str
    kind: Literal["door", "window"]
    wall: str
    offset: float = Field(ge=0)  # from the wall's start point to the opening's start edge
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    sill: float = Field(default=0.0, ge=0)


class Floor(BaseModel):
    id: str
    level: str
    boundary: list[Point] = Field(min_length=3)
    thickness: float = Field(gt=0)
    material: str = "Concrete"
    openings: list[list[Point]] = []  # holes through the slab, e.g. where a stair arrives


class Footing(BaseModel):
    """Isolated pad footing; its top is at `level` (the foundation level)."""
    id: str
    at: Point
    level: str
    width: float = Field(gt=0)  # along x
    length: float = Field(gt=0)  # along y
    thickness: float = Field(gt=0)
    material: str = "Concrete"
    design: dict = {}


class Column(BaseModel):
    id: str
    at: Point
    base_level: str
    top_level: str
    width: float = Field(gt=0)  # along x
    depth: float = Field(gt=0)  # along y
    material: str = "Concrete"
    design: dict = {}  # STAAD results, e.g. {"bars": "4-16d", "interaction_ratio": 0.59, "status": "pass"}


class Beam(BaseModel):
    id: str
    level: str  # top of beam at this level
    start: Point
    end: Point
    width: float = Field(gt=0)
    depth: float = Field(gt=0)
    material: str = "Concrete"
    design: dict = {}


class Room(BaseModel):
    id: str
    level: str
    name: str
    point: Point  # any point inside the room
    stair: bool = False  # a stair room: over the stair well, so not over a slab on upper levels


class StairRun(BaseModel):
    start: Point  # centre of the run at its first riser
    end: Point  # centre of the run at its last riser
    risers: int = Field(gt=0)


class Landing(BaseModel):
    boundary: list[Point] = Field(min_length=3)
    height: float = Field(gt=0)  # above the stair's base level


class Stair(BaseModel):
    """A dog-leg stair from base_level to top_level: straight runs joined by landings."""
    id: str
    base_level: str
    top_level: str
    riser: float = Field(gt=0)
    tread: float = Field(gt=0)
    width: float = Field(gt=0)  # of each run
    max_riser: float | None = None  # code limits the stair satisfies; Revit's stair type is set to them
    min_tread: float | None = None
    runs: list[StairRun] = Field(min_length=1)
    landings: list[Landing] = []
    notes: list[str] = []


class Building(BaseModel):
    schema_version: str = SCHEMA_VERSION
    name: str
    levels: list[Level]
    wall_types: list[WallType]
    walls: list[Wall]
    openings: list[Opening] = []
    floors: list[Floor] = []
    rooms: list[Room] = []
    columns: list[Column] = []
    beams: list[Beam] = []
    footings: list[Footing] = []
    stairs: list[Stair] = []


def _polygon_area(points):
    return 0.5 * abs(sum(points[i][0] * points[(i + 1) % len(points)][1]
                         - points[(i + 1) % len(points)][0] * points[i][1]
                         for i in range(len(points))))


def _inside(point, polygon):
    x, y = point
    inside = False
    for i in range(len(polygon)):
        (x1, y1), (x2, y2) = polygon[i], polygon[(i + 1) % len(polygon)]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _on_boundary(point, polygon, tol=1e-6):
    for i in range(len(polygon)):
        a, b = polygon[i], polygon[(i + 1) % len(polygon)]
        length = math.dist(a, b)
        if length < tol:
            continue
        cross = (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])
        t = ((point[0] - a[0]) * (b[0] - a[0]) + (point[1] - a[1]) * (b[1] - a[1])) / length ** 2
        if abs(cross) / length < tol and -tol <= t <= 1 + tol:
            return True
    return False


def _covered_by_beams(wall, beams, tol=1e-6):
    """True if collinear beams together cover the wall's whole length."""
    length = wall.length
    ux, uy = (wall.end[0] - wall.start[0]) / length, (wall.end[1] - wall.start[1]) / length
    spans = []
    for bm in beams:
        on_line = all(abs((p[0] - wall.start[0]) * uy - (p[1] - wall.start[1]) * ux) < tol for p in (bm.start, bm.end))
        if on_line:
            t = sorted((p[0] - wall.start[0]) * ux + (p[1] - wall.start[1]) * uy for p in (bm.start, bm.end))
            spans.append(t)
    reach = 0.0
    for t0, t1 in sorted(spans):
        if t0 > reach + tol:
            break
        reach = max(reach, t1)
    return reach >= length - tol


def validate_building(b: Building) -> list[str]:
    """Return a list of problems; empty means the model is consistent."""
    problems = []
    levels = {lv.id for lv in b.levels}
    wall_types = {t.id for t in b.wall_types}
    walls = {w.id: w for w in b.walls}

    for kind, items in (("level", b.levels), ("wall_type", b.wall_types), ("wall", b.walls),
                        ("opening", b.openings), ("floor", b.floors), ("room", b.rooms),
                        ("column", b.columns), ("beam", b.beams), ("footing", b.footings),
                        ("stair", b.stairs)):
        ids = [i.id for i in items]
        problems += [f"duplicate {kind} id '{i}'" for i in sorted({i for i in ids if ids.count(i) > 1})]

    for w in b.walls:
        if w.level not in levels:
            problems.append(f"wall {w.id}: unknown level '{w.level}'")
        if w.type not in wall_types:
            problems.append(f"wall {w.id}: unknown wall type '{w.type}'")
        if w.length < 1e-6:
            problems.append(f"wall {w.id}: zero length")

    for o in b.openings:
        w = walls.get(o.wall)
        if w is None:
            problems.append(f"opening {o.id}: unknown wall '{o.wall}'")
            continue
        if o.offset + o.width > w.length + 1e-6:
            problems.append(f"opening {o.id}: offset {o.offset} + width {o.width} exceeds wall {w.id} "
                            f"length {w.length:.3f}")
        if o.sill + o.height > w.height + 1e-6:
            problems.append(f"opening {o.id}: sill {o.sill} + height {o.height} exceeds wall height {w.height}")

    for f in b.floors:
        if f.level not in levels:
            problems.append(f"floor {f.id}: unknown level '{f.level}'")
        if _polygon_area(f.boundary) < 1e-6:
            problems.append(f"floor {f.id}: boundary has no area")
        for hole in f.openings:
            if len(hole) < 3 or not all(_inside(p, f.boundary) or _on_boundary(p, f.boundary) for p in hole):
                problems.append(f"floor {f.id}: opening {hole} is not inside the floor")

    for r in b.rooms:
        if r.level not in levels:
            problems.append(f"room {r.id}: unknown level '{r.level}'")
        elif not r.stair and not any(f.level == r.level and _inside(r.point, f.boundary) for f in b.floors):
            problems.append(f"room {r.id}: point {r.point} is not inside any floor on level {r.level}")

    elevation = {lv.id: lv.elevation for lv in b.levels}
    for c in b.columns:
        if c.base_level not in levels or c.top_level not in levels:
            problems.append(f"column {c.id}: unknown level")
        elif elevation[c.base_level] >= elevation[c.top_level]:
            problems.append(f"column {c.id}: base level is not below top level")

    def column_at(point, z):
        return any(math.dist(point, c.at) < 1e-6 and c.base_level in elevation and c.top_level in elevation
                   and elevation[c.base_level] < z <= elevation[c.top_level] + 1e-9 for c in b.columns)

    for bm in b.beams:
        if bm.level not in levels:
            problems.append(f"beam {bm.id}: unknown level '{bm.level}'")
            continue
        for end in (bm.start, bm.end):
            if not column_at(end, elevation[bm.level]):
                problems.append(f"beam {bm.id}: end {end} is not on a column reaching level {bm.level}")

    # With a frame present, every external wall above the lowest level must sit on a beam at its
    # level; an internal wall may instead stand on a floor slab (a partition, loaded onto the slab)
    if b.beams:
        ground = min(elevation.values())
        for w in b.walls:
            if w.level not in elevation or elevation[w.level] <= ground:
                continue
            if _covered_by_beams(w, [bm for bm in b.beams if bm.level == w.level]):
                continue
            mid = ((w.start[0] + w.end[0]) / 2, (w.start[1] + w.end[1]) / 2)
            on_slab = any(f.level == w.level and _inside(mid, f.boundary) for f in b.floors)
            if w.external or not on_slab:
                problems.append(f"wall {w.id}: not supported by a beam at level {w.level}")

    for ft in b.footings:
        if ft.level not in levels:
            problems.append(f"footing {ft.id}: unknown level '{ft.level}'")
        elif not any(c.base_level == ft.level and abs(c.at[0] - ft.at[0]) <= ft.width / 2 + 1e-6
                     and abs(c.at[1] - ft.at[1]) <= ft.length / 2 + 1e-6 for c in b.columns):
            problems.append(f"footing {ft.id}: no column starts on it at {ft.at}")
    pads = b.footings
    for i, p in enumerate(pads):
        for q in pads[i + 1:]:
            if (abs(p.at[0] - q.at[0]) < (p.width + q.width) / 2 - 1e-6
                    and abs(p.at[1] - q.at[1]) < (p.length + q.length) / 2 - 1e-6):
                problems.append(f"footings {p.id} and {q.id} overlap")

    for st in b.stairs:
        if st.base_level not in elevation or st.top_level not in elevation:
            problems.append(f"stair {st.id}: unknown level")
            continue
        rise = elevation[st.top_level] - elevation[st.base_level]
        risers = sum(r.risers for r in st.runs)
        if abs(risers * st.riser - rise) > 0.005:
            problems.append(f"stair {st.id}: {risers} risers x {st.riser} m != storey height {rise:.3f} m")
        if st.max_riser is not None and st.riser > st.max_riser + 1e-6:
            problems.append(f"stair {st.id}: riser {st.riser} m above the {st.max_riser} m limit")
        if st.min_tread is not None and st.tread < st.min_tread - 1e-6:
            problems.append(f"stair {st.id}: tread {st.tread} m below the {st.min_tread} m limit")
        for r in st.runs:
            if abs(math.dist(r.start, r.end) - (r.risers - 1) * st.tread) > 0.005:
                problems.append(f"stair {st.id}: run length {math.dist(r.start, r.end):.3f} m != "
                                f"{r.risers - 1} treads x {st.tread} m")

    return problems
