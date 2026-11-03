"""Client brief: what only the client (and the plot documents) can tell STRATA.

Fields follow the pre-BIM information requirements matrix, sections A (client) and
B (site). The chatbot fills a Brief from conversation; anything the client does not
give is filled from the matrix's 'If missing' column by `with_defaults`, and
`sources` records where every value came from, so a default never passes for an
answer. All lengths in metres.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "0.1"

Direction = Literal["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
# matrix Must items only the client can answer (A1-A4, B1, B3-B5)
MUST = {
    "building_use": "A1", "floors_now": "A2", "family": "A3", "programme": "A4",
    "site.plot": "B1", "site.north": "B3", "site.roads": "B4", "site.city": "B5",
}


class Family(BaseModel):
    persons: int = Field(ge=1)
    elderly: int = Field(0, ge=0)
    children: int = Field(0, ge=0)
    disabled: int = Field(0, ge=0)


class Programme(BaseModel):
    """Rooms for the whole house; the layout stage assigns them to floors."""
    bedrooms: int = Field(ge=1)
    bathrooms_attached: int = Field(ge=0)
    bathrooms_common: int = Field(ge=0)
    living: int = Field(1, ge=0)
    dining: bool = True
    kitchen: Literal["separate_dining", "with_dining", "with_store"] = "separate_dining"
    puja: bool = False
    study: bool = False
    store: bool = False
    wc_type: Literal["western", "indian"] = "western"


class Parking(BaseModel):
    cars: int = Field(0, ge=0)
    two_wheelers: int = Field(0, ge=0)
    covered: bool = True


class Plot(BaseModel):
    """Rectangular plot; `front` is the side facing the main road."""
    width: float = Field(gt=0)  # along the front
    depth: float = Field(gt=0)

    @property
    def area(self) -> float:
        return self.width * self.depth


class Road(BaseModel):
    side: Literal["front", "left", "right", "rear"]
    width: float = Field(gt=0)


class Site(BaseModel):
    plot: Plot | None = None
    north: Direction | None = None  # direction the plot front faces; None = unknown
    roads: list[Road] = Field(default_factory=list)
    city: str | None = None
    in_ulb: bool | None = None  # inside an urban local body (e.g. Guwahati Municipal Corporation)
    plot_level_below_road: float | None = None  # m, positive when the plot is lower than the road
    soil_bearing_capacity: float | None = None  # kN/m2
    water_table_below_foundation: bool | None = None
    water_main: bool | None = None
    sewer: bool | None = None
    power_phase: Literal[1, 3] | None = None
    storm_drain: bool | None = None

    @property
    def main_road(self) -> Road | None:
        """The widest abutting road governs height and other regulations (bye-law 24(a)(i))."""
        return max(self.roads, key=lambda r: r.width) if self.roads else None


class Brief(BaseModel):
    schema_version: str = SCHEMA_VERSION
    building_use: Literal["single_family", "home_with_shop", "rental_floors"] | None = None
    floors_now: int | None = Field(None, ge=1, le=3)  # storeys including ground: G+1 = 2
    floors_future: int | None = Field(None, ge=1, le=3)  # storeys the structure is designed for
    family: Family | None = None
    programme: Programme | None = None
    parking: Parking | None = None
    balcony_per_bedroom_floor: bool | None = None
    lift: bool | None = None
    budget: Literal["economy", "mid_range", "premium"] | None = None
    vastu: bool | None = None
    finish_level: Literal["economy", "standard", "premium"] | None = None
    electrical_loads: list[str] | None = None
    water_source: Literal["municipal", "borewell", "both"] | None = None
    rental_unit: bool | None = None
    site: Site = Field(default_factory=Site)
    sources: dict[str, str] = Field(default_factory=dict)  # field path -> "client", "upload" or "default (A2)"

    @model_validator(mode="after")
    def _future_not_below_now(self):
        if self.floors_now and self.floors_future and self.floors_future < self.floors_now:
            raise ValueError("floors_future cannot be less than floors_now")
        return self

    def missing_must(self):
        """Must items not yet answered by the client, as (field path, matrix item)."""
        return [(path, item) for path, item in MUST.items()
                if self.sources.get(path, "").startswith("default") or _get(self, path) in (None, [])]

    def with_defaults(self):
        """A copy with every unanswered field filled from the matrix defaults, sources recorded.

        The plot (B1) has no default: without it there is nothing to design on.
        """
        b = self.model_copy(deep=True)
        for path in _fields(b):
            if _get(b, path) not in (None, []) and path not in b.sources:
                b.sources[path] = "client"
        for path, (item, make) in DEFAULTS.items():
            if _get(b, path) in (None, []):
                _set(b, path, make(b))
                b.sources[path] = f"default ({item})"
        return b


# Matrix 'If missing' column. Order matters: later defaults read earlier ones.
DEFAULTS = {
    "building_use": ("A1", lambda b: "single_family"),
    "floors_now": ("A2", lambda b: 2),
    "floors_future": ("A2", lambda b: max(3, b.floors_now)),
    "family": ("A3", lambda b: Family(persons=4)),
    # 2BHK per floor (A4) with one attached + one common bathroom per floor (A5), closed kitchen (A10)
    "programme": ("A4", lambda b: Programme(
        bedrooms=2 * b.floors_now, bathrooms_attached=b.floors_now, bathrooms_common=b.floors_now,
        kitchen="separate_dining")),
    "parking": ("A6", lambda b: Parking(cars=1, covered=True)),
    "balcony_per_bedroom_floor": ("A7", lambda b: True),
    "lift": ("A7", lambda b: False),
    "budget": ("A8", lambda b: "mid_range"),
    "vastu": ("A9", lambda b: False),
    "finish_level": ("A11", lambda b: "standard"),
    "electrical_loads": ("A12", lambda b: ["AC in bedrooms", "geyser in bathrooms"]),
    "water_source": ("A13", lambda b: "municipal"),
    "rental_unit": ("A14", lambda b: False),
    "site.roads": ("B4", lambda b: [Road(side="front", width=6.0)]),
    "site.city": ("B5", lambda b: "Guwahati"),
    "site.in_ulb": ("B5", lambda b: True),
    "site.plot_level_below_road": ("B6", lambda b: 0.3),
    "site.soil_bearing_capacity": ("B7", lambda b: 100.0),
    "site.water_table_below_foundation": ("B8", lambda b: True),
    "site.water_main": ("B11", lambda b: True),
    "site.sewer": ("B11", lambda b: False),
    "site.power_phase": ("B12", lambda b: 3),
    "site.storm_drain": ("B13", lambda b: True),
}


def _fields(b):
    top = [f for f in Brief.model_fields if f not in ("schema_version", "site", "sources")]
    return top + [f"site.{f}" for f in Site.model_fields]


def _get(obj, path):
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def _set(obj, path, value):
    *parents, last = path.split(".")
    for part in parents:
        obj = getattr(obj, part)
    setattr(obj, last, value)
