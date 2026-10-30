"""Designed STAAD frame -> building JSON structural part (levels, columns, beams, slabs).

STAAD's plan is X-Z with Y up; the building JSON plan is x-y, so STAAD Z
becomes building y. Member sizes come from the frame's final design round
and each member carries its STAAD result in `design`.
"""

from src.structure.staad_results import MIN_COLUMN_STEEL_PCT, StaadResults
from src.structure.std_writer import Frame

LEVEL_NAMES = {0: "STRATA Ground Floor", 1: "STRATA First Floor", 2: "STRATA Second Floor"}
GROUND_SLAB = 0.15  # m, slab on grade


def level_id(i: int) -> str:
    return f"L{i}"


def _plan(frame, node):
    x, _, z = frame.nodes[node]
    return (x, z)


def _column_design(c, min_bar):
    if c is None:
        return {"status": "not designed", "min_main_bar_mm": min_bar}
    errors = list(c.errors)
    if c.below_min_steel:
        errors.append(f"IS 456 cl. 26.5.3.1(a): steel {c.steel_ratio_pct}% < {MIN_COLUMN_STEEL_PCT}% of gross area")
    return {"bars": c.bars, "min_main_bar_mm": min_bar, "steel_ratio_pct": c.steel_ratio_pct,
            "interaction_ratio": c.interaction_ratio, "axial_kn": c.axial_kn,
            "status": "fail" if c.failed or c.below_min_steel else "pass", "errors": errors}


def _beam_design(b):
    if b is None:
        return {"status": "not designed"}
    return {"top_steel_required_mm2": b.max_top_required, "top_steel_provided_mm2": b.max_top_provided,
            "bottom_steel_required_mm2": b.max_bottom_required,
            "bottom_steel_provided_mm2": b.max_bottom_provided,
            "min_stirrup_spacing_mm": b.min_stirrup_spacing_mm,
            "status": "fail" if b.failed else "pass", "errors": b.errors}


def outline(frame: Frame):
    xs = sorted({x for x, _, _ in frame.nodes.values()})
    ys = sorted({z for _, _, z in frame.nodes.values()})
    return [(xs[0], ys[0]), (xs[-1], ys[0]), (xs[-1], ys[-1]), (xs[0], ys[-1])]


def structure_from_frame(frame: Frame, results: StaadResults, code_note: str = "") -> dict:
    """Levels, columns, beams and slabs for the building JSON."""
    roof = frame.storeys
    levels = [{"id": level_id(i), "name": LEVEL_NAMES.get(i, "STRATA Roof") if i < roof else "STRATA Roof",
               "elevation": round(i * frame.storey_height, 6)} for i in range(roof + 1)]
    columns, beams = [], []
    for mid, m in sorted(frame.members.items()):
        sec = frame.section(mid)
        if m.kind == "column":
            columns.append({
                "id": f"C{mid}", "at": _plan(frame, m.start),
                "base_level": level_id(m.level), "top_level": level_id(m.level + 1),
                "width": sec.width, "depth": sec.depth,
                "design": _column_design(results.columns.get(mid), frame.min_bar(frame.group_of(mid))),
            })
        else:
            beams.append({
                "id": f"B{mid}", "level": level_id(m.level),
                "start": _plan(frame, m.start), "end": _plan(frame, m.end),
                "width": sec.width, "depth": sec.depth, "design": _beam_design(results.beams.get(mid)),
            })
    shape = outline(frame)
    floors = [{"id": "F0", "level": level_id(0), "boundary": shape, "thickness": GROUND_SLAB}]
    floors += [{"id": f"F{i}", "level": level_id(i), "boundary": shape, "thickness": frame.loads.slab_thickness}
               for i in range(1, roof + 1)]
    for d in [c["design"] for c in columns] + [b["design"] for b in beams]:
        if code_note:
            d["code"] = code_note
    return {"levels": levels, "columns": columns, "beams": beams, "floors": floors}
