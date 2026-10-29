"""Resize member groups from STAAD design results.

Members are sized in groups (columns per storey, beams per floor level) so
the house uses a few repeatable sizes. Each change carries the reason and
clause, for the audit trail.
"""

import copy
import math
import re

from src.structure.staad_results import _ERROR_MEMBER, StaadResults
from src.structure.std_writer import Frame, Section

# Square columns suit biaxial seismic action; 300 mm minimum per IS 13920 cl. 7.1.2
COLUMN_SIZES = [Section(d, d) for d in (0.30, 0.35, 0.40, 0.45, 0.50)]
# 230 mm beams match the brick wall; IS 13920 cl. 6.1.1 (width >= 200 mm, width/depth >= 0.3)
BEAM_WIDTH = 0.23
BEAM_DEPTHS = (0.30, 0.35, 0.38, 0.40, 0.45, 0.50, 0.60)

MAX_COLUMN_STEEL_PCT = 2.5  # practical limit, well under IS 456 cl. 26.5.3.1 maximum of 4%
MAX_BEAM_STEEL_PCT = 2.0  # practical limit, under IS 13920 cl. 6.2.2 maximum of 2.5%
# Beams-first for strong-column/weak-beam (practice, not code clauses):
SHALLOW_BEAM_MAX_STEEL_PCT = 1.0  # only make a beam group shallower if it is this lightly reinforced
MIN_DEPTH_SPAN_RATIO = 12  # practice rule D >= span / 12; STAAD's IS 456 design is the final check
MIN_BEAM_DEPTH = 0.30


def _next_column(sec):
    bigger = [s for s in COLUMN_SIZES if s.depth * s.width > sec.depth * sec.width]
    return bigger[0] if bigger else None


def _beam_depth_step(sec, step):
    depths = [d for d in BEAM_DEPTHS if (d > sec.depth if step > 0 else d < sec.depth)]
    if not depths:
        return None
    return Section(min(depths) if step > 0 else max(depths), BEAM_WIDTH)


def _beam_steel_pct(b):
    if not b.width_mm or not b.depth_mm:  # no design result for this beam
        return 100.0
    return max(b.max_top_required, b.max_bottom_required) / (b.width_mm * b.depth_mm) * 100


def _group_span(frame, group):
    spans = [math.dist(frame.nodes[m.start], frame.nodes[m.end])
             for i, m in frame.members.items() if frame.group_of(i) == group]
    return max(spans)


def resize(frame: Frame, results: StaadResults, deepened=frozenset()):
    """Return (new_frame, changes, feasible, deepened).

    `changes` is empty when everything passed. `feasible` is False when a
    failure remains that no catalogue size can fix. `deepened` is the set of
    beam groups made deeper so far in this design; they are never made
    shallower again, which stops the loop oscillating. Pass it back in on the
    next round.
    """
    new = copy.deepcopy(frame)
    changes = []
    grown = set()
    deepened = set(deepened)

    def grow(group, section, reason):
        changes.append(f"{group}: {_fmt(new.sections[group])} -> {_fmt(section)} ({reason})")
        new.sections[group] = section
        grown.add(group)

    # 1. Columns over capacity or heavily reinforced
    for member, c in sorted(results.columns.items()):
        group = frame.group_of(member)
        if group in grown:
            continue
        if c.failed or c.steel_ratio_pct > MAX_COLUMN_STEEL_PCT:
            nxt = _next_column(new.sections[group])
            if nxt is None:
                return new, changes + [f"{group}: no larger column size for member {member}"], False, deepened
            reason = _clause(c.errors[0]) if c.errors else (
                f"column {member} IR {c.interaction_ratio:.2f}, steel {c.steel_ratio_pct:.2f}%")
            grow(group, nxt, reason)

    # 2. Beams failed or heavily reinforced
    for member, b in sorted(results.beams.items()):
        group = frame.group_of(member)
        if group in grown:
            continue
        if b.failed or _beam_steel_pct(b) > MAX_BEAM_STEEL_PCT:
            nxt = _beam_depth_step(new.sections[group], +1)
            if nxt is None:
                return new, changes + [f"{group}: no deeper beam size for member {member}"], False, deepened
            reason = _clause(b.errors[0]) if b.errors else f"beam {member} steel {_beam_steel_pct(b):.2f}%"
            grow(group, nxt, reason)
            deepened.add(group)

    # 3. Strong-column/weak-beam (IS 13920 cl. 7.2): sum Mc >= 1.4 sum Mb at every joint.
    # STAAD's TRACK 2 output does not name the joint, so groups change together.
    # Beams first: a shallower, lightly reinforced beam lowers sum Mb at less cost
    # than a bigger column. Columns grow only when no beam group qualifies.
    if results.joint_strength_failed and not any(g.startswith("column-") for g in grown):
        steel = {}
        for member, b in results.beams.items():
            g = frame.group_of(member)
            steel[g] = max(steel.get(g, 0.0), _beam_steel_pct(b))
        stepped = False
        for g in sorted(x for x in new.sections if x.startswith("beam-")):
            if g in grown or g in deepened or steel.get(g, 100.0) > SHALLOW_BEAM_MAX_STEEL_PCT:
                continue
            shallower = _beam_depth_step(new.sections[g], -1)
            min_depth = max(MIN_BEAM_DEPTH, _group_span(frame, g) / MIN_DEPTH_SPAN_RATIO)
            if shallower is not None and shallower.depth >= min_depth - 1e-9:
                grow(g, shallower, f"beam steel {steel[g]:.2f}% <= {SHALLOW_BEAM_MAX_STEEL_PCT}%, "
                                   f"D >= span/{MIN_DEPTH_SPAN_RATIO} (practice); IS 13920 cl. 7.2")
                stepped = True
        if not stepped:
            for g in sorted(x for x in new.sections if x.startswith("column-")):
                nxt = _next_column(new.sections[g])
                if nxt is not None:
                    grow(g, nxt, "strong-column/weak-beam, IS 13920 cl. 7.2")
                    stepped = True
        if not stepped:
            return new, changes + ["strong-column/weak-beam cannot be met with catalogue sizes"], False, deepened

    # Errors not tied to a member (input or analysis problems) cannot be fixed by resizing
    general = [e for e in results.errors if not _ERROR_MEMBER.search(e)]
    if general:
        return new, changes + [f"STAAD errors: {general}"], False, deepened
    return new, changes, True, deepened


def _clause(error):
    """Short reason from a STAAD error: the clause it cites, or its first words."""
    m = re.search(r"CL(?:AUSE)?\.?\s*([\d.]+\s*[a-z]?\)?) OF (IS-?\s*\d+(?::\d+)?)", error)
    return f"{m.group(2)} cl. {m.group(1).strip()}" if m else " ".join(error.split()[:12])


def _fmt(sec):
    return f"{sec.width * 1000:.0f}x{sec.depth * 1000:.0f}"
