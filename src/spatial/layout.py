"""Layout JSON v0.1: the floor plans the layout stage proposes and the rule checker verifies.

Plain data, lengths in metres. Plan coordinates are in the plot frame: origin at the
plot's front-left corner seen from the main road, x along the road frontage, y into the
plot. Room polygons tile each floor plate: internal edges are wall centrelines and
external edges the outer face of the wall, so the union of the rooms is the building
footprint. Walls are optional; `derive_walls` makes them from the rooms (external walls
sit inside the footprint, internal walls on the shared edge).
"""

from typing import Literal

from pydantic import BaseModel, Field
from shapely.geometry import LineString, Polygon
from shapely.ops import linemerge, unary_union

SCHEMA_VERSION = "0.1"
EXTERNAL_WALL = 0.23  # m, one brick (practice; no code minimum for non-load-bearing infill)
INTERNAL_WALL = 0.115  # m, half brick

Point = tuple[float, float]
RoomType = Literal["living", "bedroom", "dining", "study", "kitchen", "bath_wc", "bathroom", "wc", "store", "puja",
                   "stair", "corridor", "foyer", "balcony", "utility", "parking"]


class Stair(BaseModel):
    """Dog-leg stair inside a stair room (two flights and a mid landing per storey)."""
    flight_width: float = Field(gt=0)
    riser: float = Field(gt=0)
    tread: float = Field(gt=0)
    risers_per_flight: int = Field(gt=0)
    flights: int = 2
    headroom: float | None = None


class Room(BaseModel):
    id: str
    type: RoomType
    polygon: list[Point] = Field(min_length=4)
    name: str = ""
    ceiling_height: float | None = None  # clear height if not floor-to-floor minus slab (e.g. a false ceiling)
    stair: Stair | None = None

    @property
    def shape(self) -> Polygon:
        """Snapped to 1 mm so float noise (3.6 + 7.6 != 11.2) leaves no slivers between rooms."""
        return Polygon([(round(x, 3), round(y, 3)) for x, y in self.polygon])


class Door(BaseModel):
    id: str
    between: tuple[str, str]  # two room ids, or a room id and "outside"
    at: Point  # centre of the opening, on the shared edge
    width: float = Field(gt=0)
    height: float = Field(2.1, gt=0)
    main: bool = False  # the dwelling's entrance (exit doorway)
    kind: Literal["door", "opening"] = "door"  # opening: open-plan connection with no wall (an arch or open side)


class Window(BaseModel):
    id: str
    room: str
    at: Point  # centre, on an external edge of the room
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    sill: float = Field(0.9, ge=0)
    kind: Literal["window", "ventilator"] = "window"


class Wall(BaseModel):
    id: str
    start: Point  # centreline
    end: Point
    thickness: float = Field(gt=0)
    external: bool = False

    @property
    def line(self) -> LineString:
        return LineString([self.start, self.end])


class Column(BaseModel):
    id: str
    at: Point
    width: float = 0.3
    depth: float = 0.3


class Floor(BaseModel):
    level: int  # 0 = ground floor
    name: str = ""
    floor_to_floor: float = Field(3.0, gt=0)
    slab: float = Field(0.15, gt=0)
    rooms: list[Room]
    doors: list[Door] = []
    windows: list[Window] = []
    walls: list[Wall] | None = None
    columns: list[Column] = []

    def clear_height(self, room: Room) -> float:
        """Floor to the bottom of the slab (NBC 2016 Part 3 measures room height this way)."""
        return room.ceiling_height if room.ceiling_height is not None else round(self.floor_to_floor - self.slab, 3)


class Materials(BaseModel):
    concrete_grade: float | None = None  # fck, MPa
    steel_proof_stress: float | None = None  # MPa
    steel_elongation: float | None = None  # percent
    masonry: str | None = None


class Layout(BaseModel):
    schema_version: str = SCHEMA_VERSION
    name: str
    source: dict = {}  # where the layout came from: GA run, LLM proposal, library plan id
    plot: dict | None = None  # {"width": ..., "depth": ...}
    floors: list[Floor]
    plinth_height: float | None = None
    parapet_height: float | None = None
    materials: Materials = Field(default_factory=Materials)


def derive_walls(floor: Floor, external=EXTERNAL_WALL, internal=INTERNAL_WALL) -> list[Wall]:
    """Straight wall segments along every room edge. External walls are shifted inward by half
    their thickness so their outer face is the footprint edge."""
    rooms = [r.shape for r in floor.rooms]
    footprint = unary_union(rooms)
    outline = footprint.boundary.buffer(1e-6)
    edges = []
    for poly in rooms:
        c = list(poly.exterior.coords)
        edges += [LineString([c[i], c[i + 1]]) for i in range(len(c) - 1)]
    pieces = unary_union(edges)  # nodes every crossing
    pieces = list(pieces.geoms) if hasattr(pieces, "geoms") else [pieces]
    walls = []
    for is_ext in (True, False):
        group = [p for p in pieces if p.within(outline) == is_ext]
        if not group:
            continue
        merged = linemerge(group)
        merged = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
        for line in merged:
            coords = list(line.simplify(0).coords)
            for a, b in zip(coords, coords[1:]):
                t = external if is_ext else internal
                if is_ext:
                    a, b = _shift_inward(a, b, footprint, t / 2)
                walls.append(Wall(id=f"W{floor.level}_{len(walls) + 1}", start=_round(a), end=_round(b),
                                  thickness=t, external=is_ext))
    return walls


def _shift_inward(a, b, footprint, d):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = (dx * dx + dy * dy) ** 0.5
    nx, ny = -dy / n, dx / n
    mid = ((a[0] + b[0]) / 2 + nx * 0.01, (a[1] + b[1]) / 2 + ny * 0.01)
    from shapely.geometry import Point as P
    if not footprint.contains(P(mid)):
        nx, ny = -nx, -ny
    return (a[0] + nx * d, a[1] + ny * d), (b[0] + nx * d, b[1] + ny * d)


def _round(p):
    return (round(p[0], 4), round(p[1], 4))
