"""Rule checker: the reference layout passes; each deliberately broken copy fails the right check."""

import pytest

from src.inference.spec import build_spec
from src.spatial.checker import check_layout, clear_width, format_report
from src.spatial.layout import Column, Door, Wall, Window, derive_walls
from src.spatial.sample_layout import ORIGIN, sample_brief, sample_layout
from shapely.geometry import Polygon


@pytest.fixture(scope="module")
def spec():
    return build_spec(sample_brief())


def failures(result, basis=("code", "validity")):
    return [r for r in result["results"] if r["pass"] is False and r["basis"] in basis]


def warnings(result):
    return failures(result, ("practice",))


def room(layout, level, rid):
    return next(r for r in layout.floors[level].rooms if r.id == rid)


def rewall(layout, level):
    layout.floors[level].walls = derive_walls(layout.floors[level])


def test_reference_layout_passes_everything(spec):
    r = check_layout(sample_layout(), spec)
    assert r["pass"] and r["summary"]["failures"] == 0 and r["summary"]["practice_warnings"] == 0
    assert r["summary"]["skipped"] == 0 and r["summary"]["checks"] > 200
    a = r["areas"]
    assert a["floors"][0]["built_up"] == 88.0 and a["far"] == pytest.approx(148.8 / a["plot_area"], abs=1e-3)
    assert 0 < a["carpet_total"] < a["built_up_total"]  # carpet excludes the walls
    assert "PASS" in format_report(r)


def test_clear_width_of_rectangle_and_l_shape():
    assert clear_width(Polygon([(0, 0), (4, 0), (4, 3), (0, 3)])) == pytest.approx(3.0, abs=1e-3)
    ell = Polygon([(0, 0), (4, 0), (4, 1), (1, 1), (1, 3), (0, 3)])
    assert clear_width(ell) == pytest.approx(1.0, abs=1e-3)


def test_small_bedroom_fails_with_the_nbc_clause(spec):
    lay = sample_layout()
    b = room(lay, 1, "bed3")  # 3.2 x 3.6 -> 2.0 wide: clear width under 2.1 m
    b.polygon = [(x if x < ORIGIN[0] + 5 else ORIGIN[0] + 6.8, y) for x, y in b.polygon]
    rewall(lay, 1)
    f = failures(check_layout(lay, spec))
    hit = [x for x in f if "bed3" in x["subject"] and x["check"].startswith("width")]
    assert hit and "12.2.2" in hit[0]["source"]["clause"]


def test_low_ceiling_fails(spec):
    lay = sample_layout()
    lay.floors[0].floor_to_floor = 2.8  # 2.65 m clear < 2.75 m
    f = failures(check_layout(lay, spec))
    assert any(x["category"] == "ceiling" and "12.2.1" in x["source"]["clause"] for x in f)


def test_overlap_and_setback_violations(spec):
    lay = sample_layout()
    for r in lay.floors[0].rooms:  # shift the ground floor 1 m towards the road: into the front setback
        r.polygon = [(x, y - 1.0) for x, y in r.polygon]
    k = room(lay, 0, "store")
    k.polygon = [(x, y + 0.5) for x, y in k.polygon]  # now overlaps the common bath
    f = failures(check_layout(lay, spec))
    assert any(x["check"] == "rooms do not overlap" for x in f)
    assert any(x["category"] == "setbacks" for x in f)


def test_door_problems(spec):
    lay = sample_layout()
    g = lay.floors[0]
    g.doors.append(Door(id="DX", between=("bed1", "kitchen"), at=g.doors[1].at, width=0.9))  # rooms do not touch
    g.doors.append(Door(id="DY", between=("living", "bed1"), at=(ORIGIN[0] + 4.8, ORIGIN[1] + 1.0), width=0.9))
    g.doors.append(Door(id="DZ", between=("kitchen", "nowhere"), at=g.doors[1].at, width=0.9))
    r = check_layout(lay, spec)
    checks = {(x["subject"].split()[2], x["check"]) for x in failures(r)}
    assert ("DX", "door is on the wall the two spaces share, with room for its width") in checks
    assert ("DZ", "door leads to rooms that exist") in checks
    assert any(x["note"] == "redundant door" for x in warnings(r))


def test_wc_opening_into_kitchen_fails_12_4_4(spec):
    lay = sample_layout()
    g = lay.floors[0]
    k = room(lay, 0, "kitchen")
    k.polygon = [(x if x > ORIGIN[0] + 4 else ORIGIN[0] + 3.6, y) for x, y in k.polygon]  # kitchen up to the bath
    room(lay, 0, "passage").polygon = [(ORIGIN[0] + 3.6, ORIGIN[1] + 4.0), (ORIGIN[0] + 4.8, ORIGIN[1] + 4.0),
                                        (ORIGIN[0] + 4.8, ORIGIN[1] + 8.4), (ORIGIN[0] + 3.6, ORIGIN[1] + 8.4)]
    g.doors = [d for d in g.doors if d.id != "D8"]
    g.doors.append(Door(id="D8", between=("bath_c", "kitchen"), at=(ORIGIN[0] + 3.6, ORIGIN[1] + 10.1), width=0.75))
    rewall(lay, 0)
    f = failures(check_layout(lay, spec))
    assert any("12.4.4" in x.get("source", {}).get("clause", "") for x in f)


def test_unreachable_room_and_missing_door(spec):
    lay = sample_layout()
    lay.floors[0].doors = [d for d in lay.floors[0].doors if d.id != "D7"]  # store loses its only door
    f = failures(check_layout(lay, spec))
    assert any(x["check"] == "room has a door" and "store" in x["subject"] for x in f)
    assert any(x["category"] == "circulation" and "L0 store" in x["actual"] for x in f)


def test_windows_ratio_internal_window_and_bath_ventilator(spec):
    lay = sample_layout()
    g = lay.floors[0]
    g.windows = [w for w in g.windows if w.id not in ("W3", "V2")]  # bed1 dark, common bath unventilated
    g.windows.append(Window(id="WI", room="dining", at=(ORIGIN[0] + 4.8, ORIGIN[1] + 7.0), width=0.9, height=1.2))
    f = failures(check_layout(lay, spec))
    assert any("bed1" in x["subject"] and x["check"].startswith("opening_ratio") for x in f)
    assert any("bath_c" in x["subject"] and x["check"].startswith("ventilator_area")
               and "12.4.3" in x["source"]["clause"] for x in f)
    assert any(x["subject"].startswith("Ground floor WI") and x["check"].startswith("window is on an external") for x in f)


def test_bath_without_external_wall_fails(spec):
    lay = sample_layout()
    o = ORIGIN
    st = room(lay, 0, "store")  # store becomes an inner bath; a strip by the outer wall stays a store
    st.type, st.polygon = "bath_wc", [(o[0] + 1.0, o[1] + 7.6), (o[0] + 3.6, o[1] + 7.6), (o[0] + 3.6, o[1] + 9.2),
                                      (o[0] + 1.0, o[1] + 9.2)]
    from src.spatial.layout import Room
    lay.floors[0].rooms.append(Room(id="store2", type="store", polygon=[(o[0], o[1] + 7.6), (o[0] + 1.0, o[1] + 7.6),
                                                                       (o[0] + 1.0, o[1] + 9.2), (o[0], o[1] + 9.2)]))
    rewall(lay, 0)
    f = failures(check_layout(lay, spec))
    hit = [x for x in f if x["check"] == "at least one wall opens to external air"]
    assert hit and "store" in hit[0]["subject"] and "12.4.3" in hit[0]["source"]["clause"]


def test_walls_thin_missing_and_hanging(spec):
    lay = sample_layout()
    g = lay.floors[0]
    ext = next(w for w in g.walls if w.external)
    ext.thickness = 0.15
    g.walls = [w for w in g.walls if w is ext or not (w.start[1] == w.end[1] == ORIGIN[1] + 8.4 and not w.external)]
    g.walls.append(Wall(id="WH", start=(ORIGIN[0] + 1.0, ORIGIN[1] + 1.0), end=(ORIGIN[0] + 2.0, ORIGIN[1] + 1.0),
                        thickness=0.115))
    w = warnings(check_layout(lay, spec))
    assert any(x["check"].startswith("external wall thickness") for x in w)
    assert any(x["check"] == "every room edge has a wall" for x in w)
    assert any(x["subject"].endswith("WH") and "hanging" in x["check"] for x in w)


def test_columns_floating_long_span_and_stair_moved(spec):
    lay = sample_layout()
    f1 = lay.floors[1]
    f1.columns.append(Column(id="CF", at=(ORIGIN[0] + 6.4, ORIGIN[1] + 2.4)))  # nothing below it
    lay.floors[0].columns = [c for c in lay.floors[0].columns if c.id not in ("C10", "C20")]  # 0 -> 8 m front span
    r = check_layout(lay, spec)
    f, w = failures(r), warnings(r)
    assert any(x["check"].startswith("every column stands") and "CF" in x["actual"] for x in f)
    assert any("column spacing" in x["check"] for x in w)


def test_stair_must_line_up_between_floors(spec):
    lay = sample_layout()
    f1 = lay.floors[1]
    s, bed2 = room(lay, 1, "stair"), room(lay, 1, "bed2")
    s.polygon, bed2.polygon = ([(x, y - 4.0) for x, y in s.polygon], [(x, y + 3.6) for x, y in bed2.polygon])
    rewall(lay, 1)
    f = failures(check_layout(lay, spec))
    assert any(x["check"].startswith("stair is in the same place") for x in f)
    assert f1.level == 1


def test_programme_and_materials(spec):
    lay = sample_layout()
    lay.floors[0].rooms = [r for r in lay.floors[0].rooms if r.id != "store"]
    lay.floors[0].doors = [d for d in lay.floors[0].doors if "store" not in d.between]
    lay.materials.concrete_grade = 20
    lay.materials.steel_proof_stress = 600
    f = failures(check_layout(lay, spec))
    assert any(x["category"] == "programme" and x["subject"] == "store" for x in f)
    assert any(x["subject"] == "concrete" and x["source"]["status"] == "assumed" for x in f)
    assert any(x["subject"] == "reinforcement" and "5.3.1" in x["source"]["clause"] for x in f)


def test_main_door_too_narrow_fails_part_4(spec):
    lay = sample_layout()
    lay.floors[0].doors[0].width = 0.9
    f = failures(check_layout(lay, spec))
    assert any("main entrance" in x["subject"] and "4.4.2.4.1" in x["source"]["clause"] for x in f)
