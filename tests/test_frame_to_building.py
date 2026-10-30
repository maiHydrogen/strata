"""Tests for frame -> building JSON conversion, the G+1 test house, and the v0.2 structural checks."""

import copy
import os

import pytest

from src.execution.building_schema import Building, validate_building
from src.execution.frame_to_building import structure_from_frame
from src.execution.sample_house import architecture, house_frame
from src.structure.staad_results import StaadResults, read_anl
from src.structure.std_writer import Section, make_rc_frame

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "frame_g1_eudl.anl")


def _house(results=None):
    frame = house_frame()
    b = {"schema_version": "0.2", "name": "test house"}
    b.update(structure_from_frame(frame, results or StaadResults()))
    b.update(architecture(frame))
    return frame, b


def test_one_bay_frame_carries_staad_results():
    frame = make_rc_frame([4.0], [4.0])
    s = structure_from_frame(frame, read_anl(FIXTURE), "IS 13920")
    assert [lv["elevation"] for lv in s["levels"]] == [0.0, 3.0, 6.0]
    assert len(s["columns"]) == 8 and len(s["beams"]) == 8 and len(s["floors"]) == 3
    c1 = s["columns"][0]
    assert c1["at"] == (0.0, 0.0) and (c1["base_level"], c1["top_level"]) == ("L0", "L1")
    assert c1["design"]["bars"] == "4-16d" and c1["design"]["interaction_ratio"] == pytest.approx(0.78)
    assert c1["design"]["code"] == "IS 13920"
    b9 = next(b for b in s["beams"] if b["id"] == "B9")
    assert b9["level"] == "L1" and (b9["width"], b9["depth"]) == (0.23, 0.45)
    assert b9["design"]["top_steel_required_mm2"] == pytest.approx(417.16)


def test_staad_z_becomes_plan_y():
    frame = make_rc_frame([4.0], [3.0])
    s = structure_from_frame(frame, StaadResults())
    assert {tuple(c["at"]) for c in s["columns"]} == {(0, 0), (4, 0), (0, 3), (4, 3)}


def test_designed_sizes_flow_into_columns_and_beams():
    frame = make_rc_frame([4.0], [4.0])
    frame.sections["column-0"] = Section(0.35, 0.35)
    frame.sections["beam-2"] = Section(0.35, 0.23)
    s = structure_from_frame(frame, StaadResults())
    assert {c["width"] for c in s["columns"] if c["base_level"] == "L0"} == {0.35}
    assert {b["depth"] for b in s["beams"] if b["level"] == "L2"} == {0.35}


def test_g1_house_is_valid_and_complete():
    frame, b = _house()
    assert validate_building(Building.model_validate(b)) == []
    assert len(b["columns"]) == 18 and len(b["beams"]) == 24
    assert len(b["walls"]) == 12 and len(b["openings"]) == 12 and len(b["rooms"]) == 6
    first_floor_walls = [w for w in b["walls"] if w["level"] == "L1"]
    assert all(w["height"] == pytest.approx(3.0 - 0.45) for w in first_floor_walls)


def test_wall_heights_follow_designed_beam_depth():
    frame = house_frame()
    frame.sections["beam-1"] = Section(0.35, 0.23)
    walls = architecture(frame)["walls"]
    assert all(w["height"] == pytest.approx(2.65) for w in walls if w["level"] == "L0")


def test_openings_keep_clear_of_columns():
    frame, b = _house()
    walls = {w["id"]: w for w in b["walls"]}
    cols = [c["at"] for c in b["columns"]]
    half = 0.25  # half of the largest column in the size catalogue
    for o in b["openings"]:
        w = walls[o["wall"]]
        (x1, y1), (x2, y2) = w["start"], w["end"]
        length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        for (cx, cy) in cols:
            t = ((cx - x1) * (x2 - x1) + (cy - y1) * (y2 - y1)) / length
            off_line = abs((cx - x1) * (y2 - y1) - (cy - y1) * (x2 - x1)) / length
            if off_line < 1e-6:
                assert not (o["offset"] - half < t < o["offset"] + o["width"] + half), (o["id"], (cx, cy))


def test_beam_end_off_column_is_reported():
    _, b = _house()
    b = copy.deepcopy(b)
    b["beams"][0]["end"] = (2.0, 0.0)
    assert any("not on a column" in p for p in validate_building(Building.model_validate(b)))


def test_upper_wall_without_beam_is_reported():
    _, b = _house()
    b = copy.deepcopy(b)
    b["walls"].append({"id": "L1-Z", "level": "L1", "type": "INT115", "start": (2, 0), "end": (2, 3.5),
                       "height": 2.55})
    assert any("L1-Z" in p and "not supported by a beam" in p for p in validate_building(Building.model_validate(b)))


def test_column_levels_must_rise():
    _, b = _house()
    b = copy.deepcopy(b)
    b["columns"][0]["top_level"] = "L0"
    assert any("base level is not below top level" in p for p in validate_building(Building.model_validate(b)))


def test_column_design_records_bar_size_and_min_steel_check():
    frame = house_frame()
    frame.column_bars = {"column-1": 16}
    r8 = read_anl(os.path.join(os.path.dirname(__file__), "fixtures", "g1_house_r8.anl"))
    s = structure_from_frame(frame, r8)
    ground, top = s["columns"][0]["design"], s["columns"][-1]["design"]
    assert (ground["min_main_bar_mm"], top["min_main_bar_mm"]) == (12, 16)
    assert ground["status"] == "fail"
    assert ground["errors"] == ["IS 456 cl. 26.5.3.1(a): steel 0.36% < 0.8% of gross area"]
    passing = structure_from_frame(make_rc_frame([4.0], [4.0]), read_anl(FIXTURE))["columns"][0]["design"]
    assert passing["status"] == "pass" and passing["errors"] == []
