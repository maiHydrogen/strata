"""LLM layout proposals, turned into starting genomes for the layout GA.

The one LLM step in the layout stage, and it only returns data: a sketch of each plan in
the GA's own terms (bands, passage side, stair cell, terrace end, each room's floor, the
front-to-rear order rooms fill cells). `genome_from_proposal` maps a sketch onto the genome
exactly, so the GA starts from the LLM's ideas and then repairs and improves them; all
scoring stays in ga.py and the checker, with no LLM. Room names the LLM invents are
ignored and rooms it forgets keep neutral genes, so a poor proposal costs nothing but a
weak starting point.
"""

import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, create_model

from src.brief.llm import LLMError
from src.brief.chat import first_json_object
from src.spatial.ga import HEAD, MIN_CELL, MIN_ROW, SPINE, genome_length, placed

RETRIES = 3


class Placement(BaseModel):
    room: str
    floor: int  # 0 = ground


class Sketch(BaseModel):
    """One plan sketch. `idea` is filled in by code (the focus asked for), never by the LLM."""
    idea: str = ""
    bands: int  # front-to-back strips, 3 to 5
    band_depths_m: list[float] = []  # front first; may be left empty
    passage: Literal["left", "centre", "right"]
    stair_band: int  # 1 = the band behind the front band
    stair_side: Literal["left", "right"]
    upper_terrace: Literal["front", "rear"]
    floors: list[Placement]
    order: list[str]  # rooms in the order they fill cells, front to rear


FOCUS = [
    "bedrooms upstairs with a front terrace on the upper floor",
    "one bedroom on the ground floor for elders, kitchen next to dining",
    "a deep rear band for the bedrooms with attached baths, stair near the entrance",
    "a compact plan with as little unused space as possible",
]


def sketch_schema(p):
    """Sketch model for this brief, with no open-ended lists: one fixed key per room for its floor and for
    its position, fixed keys for band depths. In testing, Gemma at temperature 0 repeated list entries
    until the token limit; a fixed set of keys gives constrained decoding nothing to loop on."""
    names = [u.name for u in placed(p)]
    per_room = {f"r{i}": (int, Field(..., alias=n)) for i, n in enumerate(names)}
    floors = create_model("FloorOfEachRoom", **per_room)
    position = create_model("PositionOfEachRoom", **per_room)  # 1 = nearest the front
    depths = create_model("BandDepths", **{f"band{k}": (float, ...) for k in range(1, 6)})
    return create_model("PlanSketch", bands=(int, ...), depths_m=(depths, ...),
                        passage=(Literal["left", "centre", "right"], ...), stair_band=(int, ...),
                        stair_side=(Literal["left", "right"], ...), upper_terrace=(Literal["front", "rear"], ...),
                        floor=(floors, ...), position=(position, ...))


def to_sketch(data, focus):
    """The fixed-key reply as a Sketch."""
    floors = [Placement(room=k, floor=v) for k, v in data["floor"].items()]
    order = [k for k, _ in sorted(data["position"].items(), key=lambda kv: kv[1])]
    depths = [data["depths_m"][f"band{k}"] for k in range(1, 6)]
    return Sketch(idea=focus, bands=data["bands"], band_depths_m=[d for d in depths if d > 0],
                  passage=data["passage"], stair_band=data["stair_band"], stair_side=data["stair_side"],
                  upper_terrace=data["upper_terrace"], floors=floors, order=order)


SYSTEM = """You are an architect proposing house plans for a small Indian plot. Plans follow one
grammar, and you only choose its parameters (a program builds and checks the actual plan):
- Each floor is cut front (road side) to back into 3 to 5 bands. A 1.2 m passage runs from the
  front band to the rear. In each band there is one room on each side of the passage (narrow
  plots: the passage runs along the right side and each band has one room).
- Ground floor: the living room takes the front band on the passage's left and holds the main
  door; every other room opens onto the passage. The stair takes one cell (band 1 or later) and
  is in the same place on every floor.
- A bedroom with an attached bath needs a deep band (about 5 m) or a wide cell; a bath and a
  store can share one cell. Kitchen and dining are on the ground floor and best in adjacent
  bands on the same side. Upper floors may leave the front or the rear band unbuilt (terrace).
Return JSON for one sensible arrangement that follows the focus you are given: for every room its
floor (0 = ground) and its position in the order rooms fill cells (1 = nearest the front); band
depths in metres front to back, 0 for bands not used."""


def prompt(p, examples=(), count=3):
    rooms = [u for u in p.units if u.type != "living"]
    lines = [f"Buildable area: {p.envelope[2]:.2f} m wide (along the road) by {p.envelope[3]:.2f} m deep; "
             f"{p.floors} storeys.",
             "Rooms to place (the living room is fixed):"]
    for u in rooms:
        fixed = " (ground floor)" if u.floor == 0 else ""
        att = " with attached bath" if u.attached else ""
        lines.append(f"- {u.name}: {u.type}{att}{fixed}")
    if examples:
        lines.append("Similar real plans from a library of South Asian houses, for ideas:")
        lines += [f"- {e}" for e in examples]
    return "\n".join(lines)


def propose(llm, p, examples=(), count=3):
    """One LLM call per focus; returns (sketches, log of calls). Invalid replies are retried with the error;
    a focus that still fails is skipped, so the GA simply gets fewer seeds."""
    schema = sketch_schema(p)
    base = prompt(p, examples)
    sketches, log = [], []
    for focus in FOCUS[:count]:
        text = f"{base}\nFocus for this proposal: {focus}."
        error = None
        for _ in range(RETRIES):
            full = text if error is None else f"{text}\n\nYour previous JSON was invalid: {error}\nReturn corrected JSON."
            try:
                raw = llm.complete_json(SYSTEM, full, schema)
            except LLMError as e:
                log.append({"focus": focus, "error": str(e)})
                break
            log.append({"focus": focus, "prompt": full, "response": raw})
            try:
                data = schema.model_validate(first_json_object(raw)).model_dump(by_alias=True)
                sketches.append(to_sketch(data, focus))
                break
            except (ValidationError, json.JSONDecodeError) as e:
                error = str(e).splitlines()[0]
    return sketches, log


def genome_from_proposal(s, p):
    """Map a sketch onto the GA genome (the inverse of ga.decode for the parameters it names)."""
    g = [0.5] * genome_length(p)
    g[0] = 0.99  # use the full buildable width
    single = p.envelope[2] < 2 * MIN_CELL + SPINE
    least = MIN_ROW if single else MIN_CELL
    n_max = max(3, min(5 if single else 4, int(p.envelope[3] / least)))
    n = min(max(s.bands, 3), n_max)
    g[1] = (n - 3 + 0.5) / max(1, n_max - 2)
    depths = [max(d, 0.5) for d in (s.band_depths_m + [p.envelope[3] / n] * n)[:n]]
    # decode gives band k the share (0.2 + g_k) / sum_j (0.2 + g_j); pick that sum S so every gene is in [0, 1]
    D = sum(depths)
    S = max(0.2 * D / min(depths), 1e-6)
    S = min(S, 1.2 * D / max(depths))
    for k in range(5):
        g[2 + k] = min(1.0, max(0.0, S * depths[k] / D - 0.2)) if k < n else 0.5
    g[7] = {"left": 0.1, "centre": 0.5, "right": 0.9}[s.passage]
    slots = [(b, side) for b in range(1, n) for side in ("L" if single else "LR")]
    want = (min(max(s.stair_band, 1), n - 1), "L" if single or s.stair_side == "left" else "R")
    idx = slots.index(want) if want in slots else 0
    g[8] = (idx + 0.5) / len(slots)
    g[9] = 0.75 if s.upper_terrace == "front" else 0.25
    floors = {f.room.strip().lower(): f.floor for f in s.floors}
    order = {name.strip().lower(): i for i, name in enumerate(s.order)}
    for i, u in enumerate(placed(p)):
        key = u.name.lower()
        if key in floors:
            g[HEAD + 2 * i] = (min(max(floors[key], 0), p.floors - 1) + 0.5) / p.floors
        if key in order:
            g[HEAD + 2 * i + 1] = (order[key] + 0.5) / max(1, len(s.order))
    return g
