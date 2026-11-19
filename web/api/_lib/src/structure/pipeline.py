"""Layout -> designed structure: frame, STAAD design loop, footings, and what to send back.

`design_structure` runs the deterministic chain on an approved layout. When the design loop
cannot make every member pass with the catalogue sizes, `layout_feedback` turns the failure
into a constraint the layout GA understands (a shorter maximum span), so the layout stage
can draw a plan the frame can carry.
"""

import copy
import math
import re
from dataclasses import dataclass, field

from src.structure.design_loop import DesignOutcome, design
from src.structure.footings import basis as footing_basis
from src.structure.footings import design_footings
from src.services.generate import tank_from_brief
from src.structure.from_layout import FrameModel, build_frame
from src.structure.std_writer import Frame

MIN_LAYOUT_SPAN = 3.0  # m: below this the layout GA cannot fit rooms (MIN_CELL 2.6 plus walls)
SPAN_STEP = 0.5  # m: each time the frame fails, the next layout's spans are this much shorter


@dataclass
class StructureResult:
    model: FrameModel
    outcome: DesignOutcome
    footings: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def passed(self):
        return self.outcome.passed


def soil_bearing(brief):
    sbc = getattr(getattr(brief, "site", None), "soil_bearing_capacity", None)
    return (sbc, "brief") if sbc else (100.0, "assumed; ask for a soil test")


def with_future_floors(layout, extra):
    """The layout with its top floor repeated `extra` times: the frame the structure is designed for when the
    client plans more storeys later (brief.floors_future). Only the built floors go into the building JSON."""
    floors = sorted(layout.floors, key=lambda f: f.level)
    top = floors[-1]
    more = [top.model_copy(deep=True, update={"level": top.level + k, "name": f"Future floor {k}"})
            for k in range(1, extra + 1)]
    return layout.model_copy(update={"floors": floors + more, "name": layout.name})


def _key(frame, mid):
    m = frame.members[mid]
    a, b = (tuple(round(v, 3) for v in frame.nodes[m.start]), tuple(round(v, 3) for v in frame.nodes[m.end]))
    return frozenset((a, b))


def transfer_design(designed: DesignOutcome, built: Frame) -> DesignOutcome:
    """Sizes and STAAD results of the frame designed with the future floors, carried to the as-built frame:
    sections by group name, member results by matching end coordinates. The footings stay those of the
    designed frame (they carry the future storeys)."""
    frame = copy.deepcopy(built)
    for g in frame.sections:
        if g in designed.frame.sections:
            frame.sections[g] = copy.deepcopy(designed.frame.sections[g])
    for g, bar in designed.frame.column_bars.items():
        if g in frame.sections:
            frame.column_bars[g] = bar
    by_key = {_key(designed.frame, mid): mid for mid in designed.frame.members}
    res = copy.deepcopy(designed.results)
    res.beams, res.columns = {}, {}
    for mid in frame.members:
        src = by_key.get(_key(frame, mid))
        if src in designed.results.beams:
            res.beams[mid] = designed.results.beams[src]
        if src in designed.results.columns:
            res.columns[mid] = designed.results.columns[src]
    return DesignOutcome(frame, res, designed.passed, designed.reason, designed.rounds)


def design_structure(layout, spec, runner, name, brief=None, max_rounds=10, on_round=None) -> StructureResult:
    tank = tank_from_brief(brief, spec) if brief is not None else None
    model = build_frame(layout, spec, tank)
    notes = list(model.notes)
    extra = max(0, (getattr(brief, "floors_future", None) or 0) - len(layout.floors)) if brief is not None else 0
    target = build_frame(with_future_floors(layout, extra), spec, tank) if extra else model
    outcome = design(target.frame, runner, name=name, max_rounds=max_rounds, on_round=on_round)
    footings = []
    if outcome.passed:
        sbc, src = soil_bearing(brief)
        col = outcome.frame.sections["column-0"]
        footings = design_footings(outcome.frame, outcome.results.reactions, sbc, min(col.width, col.depth))
        notes.append(footing_basis(sbc, src))
    if extra:
        designed_storeys = len(layout.floors) + extra
        notes.append(f"Designed for {designed_storeys} storeys (the brief plans {extra} more floor"
                     f"{'s' if extra > 1 else ''} later): STAAD ran the frame with the top floor repeated; columns, "
                     f"beams and footings carry the future storey{'s' if extra > 1 else ''}, and the stair cabin, "
                     "parapets and tank sit on the future roof in that run. Only the built floors are modelled.")
        outcome = transfer_design(outcome, model.frame)
    return StructureResult(model, outcome, footings, notes)


def _longest(frame, group=None):
    spans = [math.dist(frame.nodes[m.start], frame.nodes[m.end]) for i, m in frame.members.items()
             if m.kind == "beam" and (group is None or frame.group_of(i) == group)]
    return max(spans, default=0.0)


def layout_feedback(result: StructureResult):
    """What the layout stage must change for the frame to work, or None.

    Returns {"max_span", "reason"} when shorter spans are the remedy: a beam group that no catalogue
    depth can carry, columns that no catalogue size can carry, strong-column/weak-beam that cannot
    be met, or a loop that does not settle. Errors in the STAAD run itself are not the layout's
    doing and raise instead."""
    if result.passed:
        return None
    reason = result.outcome.reason
    if reason.startswith("STAAD errors"):
        raise RuntimeError(f"STAAD run failed, not a layout problem: {reason}")
    frame = result.outcome.frame
    m = re.match(r"(beam-\d+):", reason)
    span = _longest(frame, m.group(1)) if m else _longest(frame)
    new = math.floor((span - SPAN_STEP) * 10 + 1e-9) / 10
    if new < MIN_LAYOUT_SPAN:
        return None
    return {"max_span": new, "reason": f"{reason}; longest span {span:.2f} m, next layout limited to {new:.1f} m"}


def summary(result: StructureResult) -> dict:
    """Plain data for design.json and the web app."""
    o = result.outcome
    frame = o.frame
    res = o.results
    return {
        "passed": o.passed,
        "reason": o.reason,
        "rounds": [{"round": r.number, "sections": r.sections, "column_bars": r.bars, "passed": r.passed,
                    "changes": r.changes} for r in o.rounds],
        "sections": {g: f"{s.width * 1000:.0f}x{s.depth * 1000:.0f}" for g, s in sorted(frame.sections.items())},
        "slab_mm": round(frame.loads.slab_thickness * 1000),
        "base_shear": res.base_shear,
        "members": {"columns": len(res.columns), "beams": len(res.beams),
                    "failed": sorted([f"C{i}" for i, c in res.columns.items() if c.failed or c.below_min_steel]
                                     + [f"B{i}" for i, b in res.beams.items() if b.failed])},
        "warnings": res.warnings,
        "footings": [{"joint": f.joint, "at": f.at, "size_m": f.size, "thickness_m": f.thickness,
                      "service_kn": f.service_kn, "governing": f.governing, "bearing_kn_m2": f.pressure,
                      "allowed_kn_m2": f.allowed} for f in result.footings],
        "notes": result.notes,
    }

