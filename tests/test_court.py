"""Courtyard plan type: rooms round an open court, NBC 2016 Part 3 cl. 8.2.5 (a), open-sided verandah."""

import json
from pathlib import Path

import pytest
from shapely.geometry import Polygon
from shapely.ops import unary_union

from src.brief.schema import Brief
from src.inference.spec import build_spec
from src.spatial import court
from src.spatial.checker import check_layout
from src.spatial.ga import alternatives, decode, evaluate, problem, to_layout
from src.spatial.layout import Door, Floor, Room, derive_walls

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def designed():
    b = Brief.model_validate(json.loads((ROOT / "examples/court_60x80.brief.json").read_text())).with_defaults()
    spec = build_spec(b)
    gs, p = alternatives(b, spec, count=2, restarts=3, generations=100, seed=1)
    return b, spec, gs, p


def test_courtyard_brief_selects_the_court_plan_with_the_nbc_minimum(designed):
    _, spec, _, p = designed
    assert p.court and p.roof_stair and p.symmetric
    assert p.court_min == 3.0 and p.court_fraction == 0.2  # from the spec (cl. 8.2.5 (a))
    src = next(r for r in spec["building"] if r["element"] == "courtyard" and r["quantity"] == "width")["source"]
    assert src["clause"] == "Vol 1, Part 3, cl. 8.2.5 (a)" and src["page"] == "36"


def test_options_meet_every_hard_constraint_and_pass_the_checker(designed):
    _, spec, gs, p = designed
    for k, g in enumerate(gs, 1):
        hard, _, why, plan = evaluate(g, p, detail=True)
        assert hard == 0, why
        lay = to_layout(g, p, name=f"Option {k}")
        res = check_layout(lay, spec)
        assert res["pass"], [r for r in res["results"] if r["pass"] is False and r["basis"] != "practice"]
        court_checks = [r for r in res["results"] if r["subject"].startswith("courtyard")]
        assert len(court_checks) == 2 and all(r["pass"] for r in court_checks)


def test_court_is_a_hole_ringed_by_an_open_verandah_with_no_walls_to_it(designed):
    _, _, gs, p = designed
    lay = to_layout(gs[0], p)
    f = lay.floors[0]
    foot = unary_union([r.shape for r in f.rooms])
    assert len(foot.interiors) == 1  # the court: no room covers it
    hole = Polygon(foot.interiors[0].coords)
    ver = [r for r in f.rooms if r.open]
    assert len(ver) == 4 and all(r.type == "corridor" for r in ver)
    for w in f.walls:  # no wall stands on the court edge
        assert hole.exterior.distance(w.line.centroid) > 0.05 or not hole.exterior.buffer(0.01).contains(w.line)
    assert any(r.type == "stair" for r in f.rooms)  # the stair to the terrace on a single storey


def test_every_room_opens_onto_the_verandah_or_the_living_room(designed):
    _, _, gs, p = designed
    plan = decode(gs[0], p)
    for cell in plan.floors[0]:
        if cell.side == "LIV":
            continue
        for room in cell.rooms:
            if not room[0].startswith("Bath of "):
                assert court.reach(room, cell, plan) >= court.DOOR_REACH - 1e-6, room


def test_too_narrow_a_court_is_a_hard_violation_and_a_checker_failure(designed):
    b, spec, gs, p = designed
    g = list(gs[0])
    g[7] = 1.0  # widest side bays: the court is squeezed below 3 m wherever the decoder cannot keep it
    plan = decode(g, p)
    cx0, cy0, cx1, cy1 = plan.court
    assert cx1 - cx0 >= p.court_min - 1e-6  # the decoder keeps the court its minimum width
    narrow = Floor(level=0, rooms=[
        Room(id="a", type="corridor", polygon=[(0, 0), (6, 0), (6, 1), (0, 1)], open=True),
        Room(id="b", type="corridor", polygon=[(0, 3), (6, 3), (6, 4), (0, 4)], open=True),
        Room(id="c", type="bedroom", polygon=[(0, 1), (1, 1), (1, 3), (0, 3)]),
        Room(id="d", type="bedroom", polygon=[(5, 1), (6, 1), (6, 3), (5, 3)])])
    from src.spatial.checker import _courtyards, _requirements, _Results
    from src.spatial.layout import Layout
    res = _Results()
    _courtyards(res, Layout(name="t", floors=[narrow], parapet_height=1.0), _requirements(spec))
    width = next(r for r in res.items if "width" in r["check"])
    assert width["actual"] == 2.0 and width["pass"] is False and width["basis"] == "code"


def test_open_rooms_get_no_wall_between_them_or_to_the_court():
    rooms = [Room(id="v1", type="corridor", polygon=[(1, 1), (2, 1), (2, 5), (1, 5)], open=True),
             Room(id="v2", type="corridor", polygon=[(2, 1), (5, 1), (5, 2), (2, 2)], open=True),
             Room(id="r", type="bedroom", polygon=[(0, 0), (6, 0), (6, 1), (0, 1)])]
    walls = derive_walls(Floor(level=0, rooms=rooms))
    shared = [w for w in walls if abs(w.start[0] - 2) < 1e-6 and abs(w.end[0] - 2) < 1e-6 and min(w.start[1], w.end[1]) >= 1]
    assert not [w for w in shared if max(w.start[1], w.end[1]) <= 2 + 1e-6]  # v1|v2 shared edge: open


def test_terrace_requests_are_refused_on_a_court_plan(designed):
    from src.spatial.revise import Edit, check_edits
    _, spec, gs, p = designed
    _, no = check_edits([Edit("terrace", where="rear")], p, gs[0], spec)
    assert no and "courtyard" in no[0][1]


def test_a_plot_too_wide_for_one_passage_takes_the_court_plan():
    b = Brief.model_validate(json.loads((ROOT / "examples/court_60x80.brief.json").read_text())).with_defaults()
    b.courtyard = False
    p = problem(b, build_spec(b))
    assert p.court  # 14.7 m buildable: two rooms either side of one passage would each span over 5 m


def test_staad_member_load_lines_keep_the_load_whole():
    from src.structure.std_writer import _wrap
    line = "158 TO 162 170 183 186 TO 196 205 TO 207 211 TO 213 221 TO 223 225 TO 226 UNI GY -5.1"
    out = _wrap(line)
    assert out[-1] == "UNI GY -5.1" and all(x.endswith(" -") for x in out[:-1])


def test_windows_stand_clear_of_every_column(designed):
    """A window never runs through a column: walls are split round the frame and a short stretch gets two windows."""
    _, spec, gs, p = designed
    for g in gs:
        layout = to_layout(g, p)
        check = check_layout(layout, spec)
        rows = [r for r in check["results"] if r["check"] == "window clears the columns"]
        assert rows and all(r["pass"] for r in rows), [r for r in rows if not r["pass"]]


def test_a_window_across_a_column_fails_the_checker(designed):
    _, spec, gs, p = designed
    layout = to_layout(gs[0], p)
    fl = layout.floors[0]
    w = next(x for x in fl.windows if x.kind == "window")
    # slide the window along its wall onto the nearest column on that wall
    vertical = any(abs(c.at[0] - w.at[0]) < 1e-6 for c in fl.columns)
    on = [c for c in fl.columns if abs((c.at[0] if vertical else c.at[1]) - (w.at[0] if vertical else w.at[1])) < 1e-6]
    col = min(on, key=lambda c: abs((c.at[1] if vertical else c.at[0]) - (w.at[1] if vertical else w.at[0])))
    w.at = (w.at[0], col.at[1]) if vertical else (col.at[0], w.at[1])
    w.width = 0.6
    check = check_layout(layout, spec)
    row = next(r for r in check["results"] if r["check"] == "window clears the columns" and w.id in r["subject"])
    assert row["pass"] is False and col.id in row["actual"] and not check["pass"]
