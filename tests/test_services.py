"""Phase 6 services and finishes on the small G+1 test house of test_from_layout."""

import math

import pytest
from shapely.geometry import Point, Polygon

from src.execution.building_schema import Building, validate_building
from src.services import templates as T
from src.services.generate import add_services, tank_size
from src.structure.from_layout import build_frame
from test_from_layout import _building, _layout


@pytest.fixture(scope="module")
def house():
    _, b = _building()
    return add_services(b, tank_size(4, 3))


def test_tank_follows_nbc_population_and_half_day_storage():
    t = tank_size(4, 3)  # 3 bedrooms: NBC population 6 > the brief's 4
    assert (t["persons"], t["nbc_population"], t["required_l"], t["capacity_l"]) == (6, 6, 450, 500)
    assert tank_size(9, 5)["persons"] == 9 and tank_size(9, 5)["required_l"] == 675
    assert tank_size(9, 5)["capacity_l"] == 750
    assert t["weight_kn"] == pytest.approx(500 * 9.81 / 1000 + T.TANK_SELF_WEIGHT, abs=0.01)


def test_services_validate_and_every_check_passes(house):
    assert validate_building(Building.model_validate(house)) == []
    failed = [c for c in house["services"]["checks"] if not c["pass"]]
    assert failed == []
    assert all(c["source"]["clause"] for c in house["services"]["checks"])


def test_wet_rooms_get_their_fitments_inside_the_room(house):
    rooms = {r["id"]: r for r in house["rooms"]}
    for f in house["fixtures"]:
        if f["system"] == "plumbing":
            assert Polygon(rooms[f["room"]]["polygon"]).contains(Point(f["at"])), f["id"]
    bath = next(r for r in house["rooms"] if r["type"] == "bath_wc")
    kinds = sorted(f["kind"] for f in house["fixtures"] if f["room"] == bath["id"] and f["system"] == "plumbing")
    assert kinds == ["shower", "washbasin", "wc"]


def test_wall_fixtures_sit_on_a_wall_face_not_in_a_doorway(house):
    rooms = {r["id"]: r for r in house["rooms"]}
    for f in house["fixtures"]:
        if f["kind"] in ("socket_6a", "socket_16a", "switchboard"):
            edge = Polygon(rooms[f["room"]]["polygon"]).exterior
            assert edge.distance(Point(f["at"])) < 0.2, f["id"]  # against the face, within half a wall + box


def test_ceiling_fixtures_hang_below_the_slab(house):
    elev = {lv["id"]: lv["elevation"] for lv in house["levels"]}
    for f in house["fixtures"]:
        if f["kind"] in ("light", "fan") and f["z"] > 2.0:
            assert f["z"] + f["size"][2] <= 3.0 - 0.125 + 1e-6


def test_circuits_respect_nbc_limits_and_every_point_is_on_one(house):
    on = [p for c in house["circuits"] for p in c["points"]]
    assert len(on) == len(set(on))
    wired = {f["id"] for f in house["fixtures"] if f["kind"] in ("light", "fan", "socket_6a", "socket_16a")}
    assert set(on) == wired
    for c in house["circuits"]:
        if c["kind"] == "lighting":
            assert len(c["points"]) <= 10 and c["load_w"] <= 800
        else:
            assert len(c["points"]) <= 2
    roof = [c for c in house["circuits"] if c["level"] == "L2"]
    assert roof and all(c["board"].startswith("L1-") for c in roof)  # cabin light fed from the floor below


def test_stacks_outside_the_building_and_vent_above_the_roof(house):
    foot = Polygon(next(f for f in house["floors"] if f["level"] == "L0")["boundary"])
    for p in house["pipes"]:
        assert p["outside"] and not foot.contains(Point(p["at"]))
        assert p["z0"] == 0.0 and p["z1"] >= 3.0 + 1.5
    soil = next(p for p in house["pipes"] if p["kind"] == "soil")
    assert soil["z1"] == pytest.approx(6.0 + 1.5)


def test_tank_on_the_cabin_roof_and_its_load_in_the_frame(house):
    t = house["tanks"][0]
    assert t["level"] == "L3" and t["capacity_l"] == 500
    plain, loaded = build_frame(_layout()), build_frame(_layout(), tank=tank_size(4, 3))
    roof = lambda m: [p for p in m.frame.panels if p.y == 9.0]  # noqa: E731
    area = sum((p.x1 - p.x0) * (p.z1 - p.z0) for p in roof(plain))
    added = sum((q.dead - p.dead) * (p.x1 - p.x0) * (p.z1 - p.z0) for p, q in zip(roof(plain), roof(loaded)))
    assert added == pytest.approx(tank_size(4, 3)["weight_kn"], rel=0.01) and area > 0
    assert any("Overhead tank" in n for n in loaded.notes)


def test_finishes_for_every_typed_room(house):
    typed = {r["id"] for r in house["rooms"] if r.get("type") in T.FINISHES}
    assert {f["room"] for f in house["finishes"]} == typed
    bath = next(f for f in house["finishes"] if f["dado_height"] == 2.1)
    assert "Anti-skid" in bath["floor"]
    assert not any(f["finish_floor"] for f in house["finishes"] if "Kota" in f["floor"])


def test_layout_rooms_carry_type_and_polygon_in_building_coordinates(house):
    for r in house["rooms"]:
        assert r["type"] and len(r["polygon"]) >= 4
        assert Polygon(r["polygon"]).area > 1.0
        assert math.isfinite(r["polygon"][0][0])


def _with(prefs_brief):
    from src.services.generate import prefs_from_brief
    _, b = _building()
    return add_services(b, tank_size(4, 3), prefs_from_brief(prefs_brief.with_defaults()))


def test_client_finishes_and_points_from_the_brief():
    from src.brief.schema import Brief, ElectricalPrefs, FinishPrefs
    b = _with(Brief(finishes=FinishPrefs(floor_living="marble", bath_dado=1.2, paint="texture"),
                    electrical=ElectricalPrefs(ac_rooms=["bedroom", "living"], tv_rooms=["living", "bedroom"],
                                               geyser=False, extra_sockets=1)))
    rooms = {r["id"]: r["type"] for r in b["rooms"]}
    fin = {rooms[f["room"]]: f for f in b["finishes"]}
    assert fin["living"]["floor"].startswith("Marble") and fin["bedroom"]["floor"].startswith("Vitrified")  # A11
    assert fin["bath_wc"]["dado_height"] == 1.2 and "texture" in fin["living"]["wall"]
    uses = [(rooms[f["room"]], f.get("use")) for f in b["fixtures"] if f["kind"] in ("socket_6a", "socket_16a")]
    assert ("living", "ac") in uses and ("bedroom", "ac") in uses and ("living", "tv") in uses
    assert not any(u == "geyser" for _, u in uses)
    plain = _with(Brief())
    n = lambda bb, t: sum(1 for f in bb["fixtures"] if f["kind"] == "socket_6a" and f.get("use") != "tv"  # noqa: E731
                          and {r["id"]: r["type"] for r in bb["rooms"]}[f["room"]] == t)
    assert n(b, "bedroom") == n(plain, "bedroom") + sum(1 for r in b["rooms"] if r["type"] == "bedroom")
    assert any("client gave" in note for note in b["services"]["notes"])


def test_elderly_household_gets_accessible_heights_and_the_check_passes():
    from src.brief.schema import Brief, Family
    b = _with(Brief(family=Family(persons=5, elderly=2)))
    boards = [f for f in b["fixtures"] if f["kind"] == "switchboard"]
    assert boards and all(f["z"] == 1.0 for f in boards)
    checks = {c["check"]: c for c in b["services"]["checks"]}
    acc = checks["switches and sockets within 800-1100 mm (elderly or wheelchair users)"]
    assert acc["pass"] and acc["source"]["clause"].endswith("cl. 7.2.3")
    assert checks["sockets above 200 mm from the floor"]["pass"]
    assert any("Accessible heights" in n for n in b["services"]["notes"])


def test_stacks_clear_the_frame_and_the_check_reports_it(house):
    from src.services.generate import _clashes, _frame_outlines
    frame = _frame_outlines(house)
    assert frame and not any(_clashes(p["at"], p["diameter"], frame, slack=1e-4) for p in house["pipes"])
    check = next(c for c in house["services"]["checks"] if c["check"].startswith("stacks clear of columns"))
    assert check["pass"]


def test_a_column_on_the_stack_line_slides_the_stack_along_the_wall():
    from src.services.generate import STACK_CLEAR, _clashes, _frame_outlines
    _, plain = _building()
    before = add_services(plain, tank_size(4, 3))["pipes"]
    soil = next(p for p in before if p["kind"] == "soil")
    _, b = _building()
    b["columns"].append({"id": "CX", "at": list(soil["at"]), "base_level": "LF", "top_level": "L0",
                         "width": 0.45, "depth": 0.45})
    after = add_services(b, tank_size(4, 3))
    moved = next(p for p in after["pipes"] if p["kind"] == "soil" and set(p["serves"]) & set(soil["serves"]))
    gap = Point(moved["at"]).distance(Point(soil["at"]))
    assert 0.225 + STACK_CLEAR + moved["diameter"] / 2 - 1e-6 <= gap < 1.0  # just past the column, same wall
    assert not any(_clashes(p["at"], p["diameter"], _frame_outlines(after), slack=1e-4) for p in after["pipes"])
    assert any("slid along their wall" in n for n in after["services"]["notes"])
