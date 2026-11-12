"""Layout -> STAAD frame, wind, combinations, reactions, footings, building JSON and feedback (no STAAD needed)."""

import math
import re

import pytest

from src.execution.building_schema import Building, validate_building
from src.execution.layout_to_building import building_from_layout
from src.spatial.layout import Column, Door, Floor, Layout, Materials, Room, Stair, Window
from src.structure import wind
from src.structure.design_loop import DesignOutcome, DesignRound
from src.structure.footings import design_footings
from src.structure.from_layout import FOOTING_TOP, MASONRY, PLASTER, build_frame, stair_dead_load
from src.structure.pipeline import StructureResult, layout_feedback
from src.structure.sizing import initial_sizes, slab_thickness
from src.structure.staad_results import StaadResults, parse_reactions
from src.structure.std_writer import SERVICE_START, combinations, lateral_sets, write_std

# Grid (outer faces): x 0, 4, 8; y 0, 4, 8. Ground floor builds all 4 cells; the first floor only the
# front row (y 0..4), so the rear row is a terrace. The stair is the front-right cell on both floors.
XS, YS = (0.0, 4.0, 8.0), (0.0, 4.0, 8.0)
STAIR = Stair(flight_width=1.0, riser=0.15, tread=0.25, risers_per_flight=10)


def _rect(rid, rtype, x0, y0, x1, y1, **kw):
    return Room(id=rid, type=rtype, polygon=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)], **kw)


def _layout():
    ground = Floor(level=0, name="Ground floor", rooms=[
        _rect("living", "living", 0, 0, 4, 4), _rect("stair", "stair", 4, 0, 8, 4, stair=STAIR),
        _rect("bed1", "bedroom", 0, 4, 4, 8), _rect("kitchen", "kitchen", 4, 4, 8, 8)],
        doors=[Door(id="D1", between=("living", "outside"), at=(2, 0), width=1.0, main=True),
               Door(id="D2", between=("living", "bed1"), at=(2, 4), width=0.9)],
        windows=[Window(id="W1", room="bed1", at=(0, 6), width=1.2, height=1.2)],
        columns=[Column(id=f"C{i}{j}", at=(x, y)) for i, x in enumerate(XS) for j, y in enumerate(YS)])
    first = Floor(level=1, name="First floor", rooms=[
        _rect("bed2", "bedroom", 0, 0, 2.5, 4), _rect("bath2", "bath_wc", 2.5, 0, 4, 4),  # partition at x 2.5
        _rect("stair", "stair", 4, 0, 8, 4, stair=STAIR)],
        columns=[Column(id=f"C{i}{j}", at=(x, y)) for i, x in enumerate(XS) for j, y in enumerate(YS[:2])])
    return Layout(name="t", floors=[ground, first], plinth_height=0.45, parapet_height=1.0,
                  materials=Materials(concrete_grade=25, steel_proof_stress=500))


def _beam(frame, a, b, y):
    for i, m in frame.members.items():
        ends = {(frame.nodes[m.start][0], frame.nodes[m.start][2]), (frame.nodes[m.end][0], frame.nodes[m.end][2])}
        if m.kind == "beam" and abs(frame.nodes[m.start][1] - y) < 1e-6 and ends == {a, b}:
            return i
    raise KeyError((a, b, y))


def test_grid_moves_to_wall_centrelines_and_upper_storey_covers_its_cells_only():
    model = build_frame(_layout())
    assert model.xs == [0.115, 4.0, 7.885] and model.zs == [0.115, 4.0, 7.885]
    assert model.cells[0] == {(0, 0), (1, 0), (0, 1), (1, 1)} and model.cells[1] == {(0, 0), (1, 0)}
    assert model.stair == [{(1, 0)}, {(1, 0)}]
    f = model.frame
    assert f.base_y == -FOOTING_TOP
    kinds = [m.kind for m in f.members.values()]
    # columns: 9 footing stubs + 9 ground storey + 6 first storey + 4 stair cabin;
    # beams: 12 plinth + 12 first floor + 7 roof + 4 cabin roof
    assert kinds.count("column") == 28 and kinds.count("beam") == 35
    assert sorted(f.sections) == ["beam-0", "beam-1", "beam-2", "beam-3", "column-0", "column-1", "column-2"]
    assert f.storeys == 3 and f.cabin and model.cabin == {(1, 0)}


def test_walls_on_beams_parapets_on_roof_edges_partitions_on_the_slab():
    model = build_frame(_layout())
    f = model.frame
    face = MASONRY * 0.23 + PLASTER
    # ground-floor rear wall (no openings) on the plinth beam
    rear = _beam(f, (0.115, 7.885), (4.0, 7.885), 0.0)
    assert f.wall_faces[rear] == [(pytest.approx(face, rel=1e-3), "storey")]
    # terrace rear edge at level 1: parapet; level 1 front edge carries the first-floor wall instead
    assert f.wall_faces[_beam(f, (0.115, 7.885), (4.0, 7.885), 3.0)] == [(pytest.approx(face, rel=1e-3), "parapet")]
    assert all(k == "storey" for _, k in f.wall_faces[_beam(f, (0.115, 0.115), (4.0, 0.115), 3.0)])
    # roof: parapet all round, including the edge over the terrace
    assert f.wall_faces[_beam(f, (0.115, 4.0), (4.0, 4.0), 6.0)][0][1] == "parapet"
    # the window takes its area off the wall it is in
    left = _beam(f, (0.115, 4.0), (0.115, 7.885), 0.0)
    assert f.wall_faces[left][0][0] < face
    panels = {(p.y, p.x0, p.z0): p for p in f.panels}
    terrace = panels[(3.0, 0.115, 4.0)]
    assert terrace.kind == "roof" and terrace.live == 1.5
    bedroom = panels[(3.0, 0.115, 0.115)]  # carries the x = 2.5 partition
    assert bedroom.kind == "floor" and bedroom.dead >= 0.125 * 25 + 1.0 + 1.0
    assert panels[(3.0, 4.0, 0.115)].kind == "stair" and panels[(3.0, 4.0, 0.115)].dead == stair_dead_load(STAIR)
    assert panels[(6.0, 4.0, 0.115)].kind == "stair"  # the stair goes on to the roof


def test_storey_over_nothing_is_rejected():
    lay = _layout()
    lay.floors[1].rooms.append(_rect("bal", "balcony", 0, 8, 4, 10))
    lay.floors[1].columns += [Column(id="X1", at=(0, 10)), Column(id="X2", at=(4, 10)),
                              Column(id="X3", at=(0, 8)), Column(id="X4", at=(4, 8))]
    with pytest.raises(ValueError, match="no storey below"):
        build_frame(lay)


def test_std_has_panel_loads_wind_orthogonal_and_service_combinations():
    model = build_frame(_layout())
    text = write_std(model.frame, "t")
    assert all(len(line) <= 79 for line in text.splitlines())
    assert "XRANGE" in text and "ZRANGE" in text
    assert "LOAD 5 LOADTYPE WIND TITLE WL X" in text and "LOAD 6 LOADTYPE WIND TITLE WL Z" in text
    combos = re.findall(r"^LOAD COMB (\d+) (.+)$", text, re.M)
    # 1 gravity + 8 seismic directions (100% + 30%) x 3 + 4 wind x 3, then 5 service combinations
    assert len([c for c in combos if int(c[0]) < SERVICE_START]) == 1 + 24 + 12
    assert [c[1] for c in combos if int(c[0]) >= SERVICE_START][0] == "DL+LL"
    assert text.index("PRINT SUPPORT REACTION") < text.index("START CONCRETE DESIGN")
    assert re.search(r"^1 TO 9 FIXED$", text, re.M)


def test_orthogonal_sets_follow_is1893():
    model = build_frame(_layout())
    sets = dict(lateral_sets(model.frame))
    assert sets["+(EQX+0.3EQZ)"] == {1: 1, 2: 0.3}
    assert sets["-(EQX+0.3EQZ)"] == {1: -1, 2: -0.3}
    assert sets["-(EQZ-0.3EQX)"] == {2: -1, 1: 0.3}
    combos = dict(combinations(model.frame))
    assert combos["0.9DL-1.5(EQX+0.3EQZ)"] == {3: 0.9, 1: -1.5, 2: -0.45}


def test_wind_pressure_and_storey_forces():
    assert wind.k2(8) == 0.91 and wind.k2(12.5) == pytest.approx(0.94)
    pz = 0.6 * (50 * 0.91) ** 2 / 1000
    assert wind.design_pressure(5, 50) == pytest.approx(0.81 * pz)
    model = build_frame(_layout())
    f = model.frame
    total_x = sum(f.wind["X"].values())
    # windward face (x = 0.115): ground storey 7.77 m wide, first storey 3.885 m, parapet over the first storey;
    # the stair cabin's face at x = 4.0 is 3.885 m wide
    pd = {lvl: wind.design_pressure(lvl * 3.0 + 0.45 + (1.0 if lvl == 2 else 0), 50) for lvl in range(4)}
    expect = (pd[0] * 1.5 * 7.77 + pd[1] * (1.5 * 7.77 + 1.5 * 3.885)
              + pd[2] * (1.5 * 3.885 + 1.0 * 3.885 + 1.5 * 3.885) + pd[3] * 1.5 * 3.885)
    assert total_x == pytest.approx(expect, rel=1e-6)


def test_initial_sizes_and_slab():
    t, note = slab_thickness([(3.6, 4.0), (2.0, 5.0)])
    assert t == 0.13 and "24.1" in note
    model = build_frame(_layout())
    f = model.frame
    assert f.sections["beam-0"].depth == 0.35  # 3.885 m span / 12 = 0.32 -> 0.35
    assert f.sections["column-0"].depth >= f.sections["column-1"].depth >= 0.30
    assert any("IS 456 cl. 39.3" in n for n in initial_sizes(f))


REACTIONS = """
   SUPPORT REACTIONS -UNIT KN   METE    STRUCTURE TYPE = SPACE
   -----------------

 JOINT  LOAD   FORCE-X   FORCE-Y   FORCE-Z     MOM-X     MOM-Y     MOM Z


      1  201      2.55    152.99      5.04      2.65     -0.00     -1.15
         202     -8.08    115.88      5.64      3.28     -0.00     10.43
      2  201     -1.61    158.31      0.94      0.75      0.00      0.81
 ************** END OF LATEST ANALYSIS RESULT **************
     STAAD SPACE                                              -- PAGE NO.   17
   SUPPORT REACTIONS -UNIT KN   METE    STRUCTURE TYPE = SPACE
   -----------------

 JOINT  LOAD   FORCE-X   FORCE-Y   FORCE-Z     MOM-X     MOM-Y     MOM Z

         202    -18.16    174.36      4.04      2.25      0.14     14.89

   396. LOAD LIST 101 TO 137
"""


def test_support_reactions_parse_across_a_page_break():
    r = parse_reactions(REACTIONS)
    assert r[1][201][1] == 152.99 and r[1][202][0] == -8.08
    assert r[2][201][1] == 158.31 and r[2][202][1] == 174.36


def _fake_reactions(frame, fy=150.0):
    return {j: {201: (0, fy, 0, 0, 0, 0), 202: (0, fy * 1.2, 0, 0, 0, 0)}
            for j, (_, y, _) in frame.nodes.items() if abs(y - frame.base_y) < 1e-6}


def test_footings_are_sized_on_bearing_and_combined_when_they_would_overlap():
    model = build_frame(_layout())
    f = model.frame
    pads = design_footings(f, _fake_reactions(f, 150.0), sbc=100.0, column_size=0.3)
    assert len(pads) == 9 and all(p.width == p.length == 1.3 for p in pads)  # sqrt(150 * 1.1 / 100) = 1.28
    big = design_footings(f, _fake_reactions(f, 1500.0), sbc=100.0, column_size=0.3)
    assert len(big) < 9  # 4 m bays cannot hold 4.1 m pads side by side
    for i, p in enumerate(big):
        for q in big[i + 1:]:
            assert (abs(p.at[0] - q.at[0]) >= (p.width + q.width) / 2
                    or abs(p.at[1] - q.at[1]) >= (p.length + q.length) / 2)


def test_building_json_from_layout_validates():
    lay = _layout()
    model = build_frame(lay)
    res = StaadResults()
    res.reactions = _fake_reactions(model.frame)
    outcome = DesignOutcome(model.frame, res, True)
    pads = design_footings(model.frame, res.reactions, 100.0, 0.3)
    b = building_from_layout(lay, model, outcome, pads, "test")
    assert validate_building(Building.model_validate(b)) == []
    assert [lv["id"] for lv in b["levels"]] == ["LF", "L0", "L1", "L2", "L3"]
    assert [lv["name"] for lv in b["levels"]][-2:] == ["STRATA Roof", "STRATA Stair Cabin Roof"]
    f1 = next(f for f in b["floors"] if f["id"] == "F1")
    # the stair cell is on the building edge: a notch in the slab outline, not a hole touching it
    assert f1["openings"] == [] and len(f1["boundary"]) == 6 and (4.0, 0.0) in f1["boundary"]
    beam1 = model.frame.sections["beam-1"].depth
    walls0 = [w for w in b["walls"] if w["level"] == "L0"]
    assert all(w["height"] == pytest.approx(3.0 - beam1) for w in walls0)  # every ground wall is on a grid line
    part = next(w for w in b["walls"] if w["level"] == "L1" and w["start"][0] == w["end"][0] == 2.5)
    assert part["height"] == pytest.approx(3.0 - model.frame.loads.slab_thickness)
    assert all(math.isclose(min(abs(w["start"][1] - z) for z in model.zs), 0) for w in b["walls"]
               if w["start"][1] == w["end"][1])


def _building():
    lay = _layout()
    model = build_frame(lay)
    res = StaadResults()
    res.reactions = _fake_reactions(model.frame)
    b = building_from_layout(lay, model, DesignOutcome(model.frame, res, True),
                             design_footings(model.frame, res.reactions, 100.0, 0.3), "test")
    return model, b


def test_stair_cabin_in_the_frame():
    model = build_frame(_layout())
    f = model.frame
    face = MASONRY * 0.23 + PLASTER
    # cabin walls on all four roof beams of the stair cell, instead of a parapet on its outer edges
    for a, c in (((4.0, 0.115), (7.885, 0.115)), ((7.885, 0.115), (7.885, 4.0)), ((4.0, 4.0), (7.885, 4.0)),
                 ((4.0, 0.115), (4.0, 4.0))):
        assert f.wall_faces[_beam(f, a, c, 6.0)] == [(pytest.approx(face, rel=1e-3), "storey")]
    cabin_roof = [p for p in f.panels if p.y == 9.0]
    assert len(cabin_roof) == 1 and cabin_roof[0].kind == "roof" and cabin_roof[0].live == 0.75
    _beam(f, (4.0, 0.115), (4.0, 4.0), 9.0)  # cabin roof beam exists
    assert any("Stair cabin" in n for n in model.notes)


def test_parapets_and_cabin_walls_are_joined_runs():
    model = build_frame(_layout())
    assert sorted(model.parapets) == [
        (1, (0.115, 4.0), (0.115, 7.885)), (1, (0.115, 7.885), (7.885, 7.885)), (1, (7.885, 4.0), (7.885, 7.885)),
        (2, (0.115, 0.115), (0.115, 4.0)), (2, (0.115, 0.115), (4.0, 0.115)), (2, (0.115, 4.0), (4.0, 4.0))]
    assert len(model.cabin_walls) == 4 and {lv for lv, _, _ in model.cabin_walls} == {2}


def test_building_json_has_parapets_cabin_and_stairs():
    model, b = _building()
    assert validate_building(Building.model_validate(b)) == []
    parapets = [w for w in b["walls"] if w.get("parapet")]
    assert len(parapets) == 6 and all(w["height"] == 1.0 and w["type"] == "EXT230" for w in parapets)
    cabin = [w for w in b["walls"] if w["level"] == "L2" and not w.get("parapet")]
    assert len(cabin) == 4 and all(w["height"] == pytest.approx(3.0 - model.frame.sections["beam-3"].depth)
                                   for w in cabin)
    door = next(o for o in b["openings"] if o["wall"] in {w["id"] for w in cabin})
    assert door["kind"] == "door" and door["width"] == 0.9
    f3 = next(f for f in b["floors"] if f["level"] == "L3")
    assert f3["boundary"] == [(3.885, 0.0), (8.0, 0.0), (8.0, 4.115), (3.885, 4.115)]
    assert any(r["name"] == "Stair cabin" and r["level"] == "L2" and r["stair"] for r in b["rooms"])
    assert [(s["base_level"], s["top_level"]) for s in b["stairs"]] == [("L0", "L1"), ("L1", "L2")]


def test_stairs_carry_the_nbc_limits_and_breaking_them_is_flagged():
    """NBC 2016 Part 3 cl. 12.18.1.2-3; the Revit builder sets its stair type to these limits."""
    _, b = _building()
    assert all((s["max_riser"], s["min_tread"]) == (0.19, 0.25) for s in b["stairs"])
    b["stairs"][0]["min_tread"] = 0.28
    b["stairs"][1]["max_riser"] = 0.14
    problems = validate_building(Building.model_validate(b))
    assert any("ST0: tread 0.25 m below the 0.28 m limit" in p for p in problems)
    assert any("ST1: riser 0.15 m above the 0.14 m limit" in p for p in problems)


def test_dog_leg_geometry_fits_the_room_and_starts_away_from_the_outer_wall():
    _, b = _building()
    st = b["stairs"][0]
    r1, r2 = st["runs"]
    assert (r1["risers"], r2["risers"]) == (10, 10) and st["riser"] == pytest.approx(0.15) and st["width"] == 1.0
    # flights along x from the inner side (x = 4.0, the outer wall is at x = 7.885), back along the far wall
    assert r1["start"][0] == pytest.approx(4.0 + 0.0695) and r1["end"][0] == pytest.approx(4.0 + 0.0695 + 2.25)
    assert r2["end"][0] == pytest.approx(r1["start"][0]) and r2["start"][1] > r1["start"][1] + 1.0
    land = st["landings"][0]
    assert land["height"] == pytest.approx(1.5)
    xs = [p[0] for p in land["boundary"]]
    assert min(xs) == pytest.approx(r1["end"][0]) and max(xs) == pytest.approx(7.885 - 0.1285)
    assert st["notes"] == []  # 1.44 m landing, deeper than the 1.0 m flights


def test_stair_that_does_not_fit_is_rejected():
    from src.execution.layout_to_building import stair_geometry
    with pytest.raises(ValueError, match="does not fit"):
        stair_geometry((0, 2.0, 0, 2.0), dict.fromkeys("WESN", 0.07), STAIR, 3.0)


def _failed(model, reason):
    return StructureResult(model, DesignOutcome(model.frame, StaadResults(), False, reason=reason,
                                                rounds=[DesignRound(1, {}, {}, False, [], [])]))


def test_feedback_shortens_spans_or_gives_up():
    model = build_frame(_layout())
    fb = layout_feedback(_failed(model, "beam-1: no deeper beam size for member 30"))
    assert fb["max_span"] == 3.3  # longest beam-1 span 3.885 m - 0.5, down to 0.1 m
    with pytest.raises(RuntimeError):
        layout_feedback(_failed(model, "STAAD errors: ['bad input']"))
    ok = StructureResult(model, DesignOutcome(model.frame, StaadResults(), True))
    assert layout_feedback(ok) is None


def test_ga_respects_a_shorter_maximum_span():
    from src.inference.spec import build_spec
    from src.spatial.ga import decode, genome_length, problem
    from src.spatial.sample_layout import sample_brief
    brief = sample_brief().with_defaults()
    p = problem(brief, build_spec(brief), max_span=3.5)
    assert p.max_span == 3.5
    plan = decode([0.5] * genome_length(p), p)
    assert max(b - a for a, b in zip(plan.bands, plan.bands[1:])) <= 3.5 + 1e-6 or plan.bands[-1] >= p.envelope[3] - 1e-6
