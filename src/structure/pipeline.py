"""Layout -> designed structure: frame, STAAD design loop, footings, and what to send back.

`design_structure` runs the deterministic chain on an approved layout. When the design loop
cannot make every member pass with the catalogue sizes, `layout_feedback` turns the failure
into a constraint the layout GA understands (a shorter maximum span), so the layout stage
can draw a plan the frame can carry.
"""

import math
import re
from dataclasses import dataclass, field

from src.structure.design_loop import DesignOutcome, design
from src.structure.footings import basis as footing_basis
from src.structure.footings import design_footings
from src.structure.from_layout import FrameModel, build_frame

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


def design_structure(layout, spec, runner, name, brief=None, max_rounds=10) -> StructureResult:
    model = build_frame(layout, spec)
    outcome = design(model.frame, runner, name=name, max_rounds=max_rounds)
    notes = list(model.notes)
    footings = []
    if outcome.passed:
        sbc, src = soil_bearing(brief)
        col = outcome.frame.sections["column-0"]
        footings = design_footings(outcome.frame, outcome.results.reactions, sbc, min(col.width, col.depth))
        notes.append(footing_basis(sbc, src))
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

