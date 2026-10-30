"""Resize member groups from STAAD design results.

Members are sized in groups (columns per storey, beams per floor level) so
the house uses a few repeatable sizes. Column groups also carry a minimum
main bar diameter. Each change carries the reason and clause, for the
audit trail.
"""

import copy
import math
import re

from src.structure.staad_results import MIN_COLUMN_STEEL_PCT, StaadResults
from src.structure.std_writer import BAR_SIZES, Frame, Section

# Square columns suit biaxial seismic action; 300 mm minimum per IS 13920 cl. 7.1.2
COLUMN_SIZES = [Section(d, d) for d in (0.30, 0.35, 0.40, 0.45, 0.50)]
# 230 mm beams match the brick wall; IS 13920 cl. 6.1.1 (width >= 200 mm, width/depth >= 0.3)
BEAM_WIDTH = 0.23
BEAM_DEPTHS = (0.30, 0.35, 0.38, 0.40, 0.45, 0.50, 0.60)
# Minimum main bar diameter steps for columns, mm (STAAD MINMAIN); IS 456 cl. 26.5.3.1(c) >= 12 mm
COLUMN_BARS = BAR_SIZES

MAX_COLUMN_STEEL_PCT = 2.5  # practical limit, well under IS 456 cl. 26.5.3.1 maximum of 4%
MAX_BEAM_STEEL_PCT = 2.0  # practical limit, under IS 13920 cl. 6.2.2 maximum of 2.5%
# Shallower beams for strong-column/weak-beam (practice, not code clauses):
SHALLOW_BEAM_MAX_STEEL_PCT = 1.0  # only make a beam group shallower if it is this lightly reinforced
MIN_DEPTH_SPAN_RATIO = 12  # practice rule D >= span / 12; STAAD's IS 456 design is the final check
MIN_BEAM_DEPTH = 0.30


def _next_column(sec):
    bigger = [s for s in COLUMN_SIZES if s.depth * s.width > sec.depth * sec.width]
    return bigger[0] if bigger else None


def _bar_dia(bars):
    """'8-16d' -> 16; 0 when STAAD gave no bars."""
    m = re.match(r"\d+-(\d+)d", bars)
    return int(m.group(1)) if m else 0


def _next_bar(frame, group, provided=0):
    """Next bar size above both the group's minimum and the bar STAAD already provided."""
    bigger = [d for d in COLUMN_BARS if d > max(frame.min_bar(group), provided)]
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

    def more_bars(group, members, reason):
        provided = max((_bar_dia(results.columns[m].bars) for m in members if m in results.columns), default=0)
        bar = _next_bar(new, group, provided)
        if bar is None:
            return False
        changes.append(f"{group}: bars {max(new.min_bar(group), provided)}d -> {bar}d min ({reason})")
        new.column_bars[group] = bar
        grown.add(group)
        return True

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

    # 2. Columns under the IS 456 minimum steel: bigger bars, same section
    for member, c in sorted(results.columns.items()):
        group = frame.group_of(member)
        if group in grown or not c.below_min_steel:
            continue
        if not more_bars(group, [member], f"IS 456 cl. 26.5.3.1(a): column {member} steel {c.steel_ratio_pct:.2f}% "
                                f"< {MIN_COLUMN_STEEL_PCT}% of gross area"):
            return new, changes + [f"{group}: no larger bar size for {MIN_COLUMN_STEEL_PCT}% steel"], False, deepened

    # 3. Beams failed or heavily reinforced
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

    # 4. Strong-column/weak-beam (IS 13920 cl. 7.2.1): sum Mc >= 1.4 sum Mb at every joint,
    # roof joints included (cl. 7.2.1.3 waives them only above 4 storeys). Only the
    # members at failing joints change, cheapest first: bigger column bars, then
    # shallower lightly reinforced beams at those joints, then a bigger column section.
    if results.joint_strength_failed:
        failing = results.failed_joints
        where, members, beams_at = {}, {}, {}  # column group -> {joint: dirs}, its members, beam groups there
        if failing:
            for j in failing:  # each joint is listed once per column meeting there
                for m in (j.column, j.other_column):
                    if m in frame.members:
                        g = frame.group_of(m)
                        dirs = where.setdefault(g, {}).setdefault(j.joint, set())
                        dirs.update(j.failed_directions)
                        members.setdefault(g, set()).add(m)
                        beams_at.setdefault(g, set()).update(
                            frame.group_of(b) for b in j.failed_beams if b in frame.members)
        else:  # STAAD's joint report is missing: treat every joint as failing
            all_beams = {g for g in new.sections if g.startswith("beam-")}
            for m, mem in frame.members.items():
                if mem.kind == "column":
                    where.setdefault(frame.group_of(m), {})
                    members.setdefault(frame.group_of(m), set()).add(m)
                    beams_at[frame.group_of(m)] = all_beams
        stepped = any(g in grown for g in where)  # already changed this round: see the effect first
        out_of_bars = []
        reasons = {}
        for g in sorted(where):
            if g in grown:
                continue
            at = ", ".join(f"{jt} {''.join(sorted(d))}" for jt, d in sorted(where[g].items())) or "all (no joint report)"
            reasons[g] = f"IS 13920 cl. 7.2.1, sum Mc < 1.4 sum Mb at joint {at}"
            if more_bars(g, members[g], reasons[g]):
                stepped = True
            else:
                out_of_bars.append(g)
        if out_of_bars:
            steel = {}
            for member, b in results.beams.items():
                bg = frame.group_of(member)
                steel[bg] = max(steel.get(bg, 0.0), _beam_steel_pct(b))
            made_shallower = False
            for bg in sorted(set().union(*(beams_at.get(g, set()) for g in out_of_bars))):
                if bg in grown or bg in deepened or steel.get(bg, 100.0) > SHALLOW_BEAM_MAX_STEEL_PCT:
                    continue
                shallower = _beam_depth_step(new.sections[bg], -1)
                min_depth = max(MIN_BEAM_DEPTH, _group_span(frame, bg) / MIN_DEPTH_SPAN_RATIO)
                if shallower is not None and shallower.depth >= min_depth - 1e-9:
                    grow(bg, shallower, f"beam steel {steel[bg]:.2f}% <= {SHALLOW_BEAM_MAX_STEEL_PCT}%, "
                                        f"D >= span/{MIN_DEPTH_SPAN_RATIO} (practice); IS 13920 cl. 7.2.1")
                    made_shallower = stepped = True
            if not made_shallower:
                for g in out_of_bars:
                    if (nxt := _next_column(new.sections[g])) is not None:
                        grow(g, nxt, reasons[g])
                        stepped = True
        if not stepped:
            return new, changes + ["strong-column/weak-beam cannot be met with catalogue sizes"], False, deepened

    # 5. Practice: a storey's columns are never smaller than the columns they carry
    _stack_columns(new, changes, grown)

    # Errors not tied to a member (input or analysis problems) cannot be fixed by resizing
    if results.general_errors:
        return new, changes + [f"STAAD errors: {results.general_errors}"], False, deepened
    return new, changes, True, deepened


def _stack_columns(frame, changes, grown):
    """Grow lower-storey column groups to at least the size of the storey above, top down."""
    for level in range(frame.storeys - 2, -1, -1):
        lower, upper = f"column-{level}", f"column-{level + 1}"
        lo, up = frame.sections[lower], frame.sections[upper]
        if lo.depth * lo.width < up.depth * up.width - 1e-12:
            changes.append(f"{lower}: {_fmt(lo)} -> {_fmt(up)} (practice: not smaller than {upper} above it)")
            frame.sections[lower] = up
            grown.add(lower)


def _clause(error):
    """Short reason from a STAAD error: the clause it cites, or its first words."""
    m = re.search(r"CL(?:AUSE)?\.?\s*([\d.]+\s*[a-z]?\)?) OF (IS-?\s*\d+(?::\d+)?)", error)
    return f"{m.group(2)} cl. {m.group(1).strip()}" if m else " ".join(error.split()[:12])


def _fmt(sec):
    return f"{sec.width * 1000:.0f}x{sec.depth * 1000:.0f}"
