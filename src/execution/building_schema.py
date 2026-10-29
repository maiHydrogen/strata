"""Building JSON v0: the single source of truth handed to the Revit builder.

Plain data only, all lengths in metres, plan coordinates (x, y) with z up.
`validate_building` runs the cross-checks in WSL before anything reaches
Revit, so the builder can assume a consistent model.
"""

import math
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "0.1"

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


class Room(BaseModel):
    id: str
    level: str
    name: str
    point: Point  # any point inside the room


class Building(BaseModel):
    schema_version: str = SCHEMA_VERSION
    name: str
    levels: list[Level]
    wall_types: list[WallType]
    walls: list[Wall]
    openings: list[Opening] = []
    floors: list[Floor] = []
    rooms: list[Room] = []


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


def validate_building(b: Building) -> list[str]:
    """Return a list of problems; empty means the model is consistent."""
    problems = []
    levels = {lv.id for lv in b.levels}
    wall_types = {t.id for t in b.wall_types}
    walls = {w.id: w for w in b.walls}

    for kind, items in (("level", b.levels), ("wall_type", b.wall_types), ("wall", b.walls),
                        ("opening", b.openings), ("floor", b.floors), ("room", b.rooms)):
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

    for r in b.rooms:
        if r.level not in levels:
            problems.append(f"room {r.id}: unknown level '{r.level}'")
        elif not any(f.level == r.level and _inside(r.point, f.boundary) for f in b.floors):
            problems.append(f"room {r.id}: point {r.point} is not inside any floor on level {r.level}")

    return problems
