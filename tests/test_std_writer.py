"""Tests for the STAAD .std generator."""

import re

import pytest

from src.structure.std_writer import Section, _id_list, beam_gravity_udl, make_rc_frame, write_std


def _section(text, start, end):
    lines = text.splitlines()
    i = lines.index(start)
    j = lines.index(end, i + 1)
    return lines[i + 1:j]


def test_one_bay_g1_frame_geometry():
    frame = make_rc_frame([4.0], [4.0], storeys=2)
    assert len(frame.nodes) == 12
    kinds = [m.kind for m in frame.members.values()]
    assert kinds.count("column") == 8 and kinds.count("beam") == 8
    for m in frame.members.values():
        (x1, y1, z1), (x2, y2, z2) = frame.nodes[m.start], frame.nodes[m.end]
        if m.kind == "column":
            assert (x1, z1) == (x2, z2) and y2 - y1 == 3.0
        else:
            assert y1 == y2 > 0


def test_larger_grid_counts():
    frame = make_rc_frame([3.5, 4.0], [3.0, 3.0, 4.0], storeys=3)
    nodes_per_level = 3 * 4
    assert len(frame.nodes) == nodes_per_level * 4
    columns = sum(m.kind == "column" for m in frame.members.values())
    beams = sum(m.kind == "beam" for m in frame.members.values())
    assert columns == nodes_per_level * 3
    assert beams == 3 * (4 * 2 + 3 * 3)


def test_std_has_all_blocks_in_order():
    text = write_std(make_rc_frame([4.0], [4.0]))
    order = ["STAAD SPACE", "JOINT COORDINATES", "MEMBER INCIDENCES", "DEFINE MATERIAL START",
             "MEMBER PROPERTY INDIAN", "SUPPORTS", "DEFINE 1893 LOAD", "LOAD 1 LOADTYPE SEISMIC TITLE EQ X",
             "PERFORM ANALYSIS PRINT STATICS CHECK", "START CONCRETE DESIGN", "END CONCRETE DESIGN", "FINISH"]
    positions = [text.index(tok) for tok in order]
    assert positions == sorted(positions)
    assert len(_section(text, "JOINT COORDINATES", "MEMBER INCIDENCES")) == 12
    assert "1 TO 4 FIXED" in text


def test_load_combinations_follow_is456_is1893():
    text = write_std(make_rc_frame([4.0], [4.0]))
    combos = re.findall(r"^LOAD COMB (\d+) (.+)$", text, re.M)
    assert len(combos) == 13
    assert combos[0] == ("101", "1.5(DL+LL)")
    assert "LOAD LIST 101 TO 113" in text


def test_seismic_weight_excludes_roof_live_load():
    frame = make_rc_frame([4.0], [4.0])
    text = write_std(frame)
    weights = _section(text, "FLOOR WEIGHT", "LOAD 1 LOADTYPE SEISMIC TITLE EQ X")
    dead = frame.loads.floor_dead()
    assert weights[0].endswith(f"FLOAD {dead + 0.25 * frame.loads.floor_live:g}")
    assert weights[1].endswith(f"FLOAD {dead:g}")


def test_ductile_switch():
    frame = make_rc_frame([4.0], [4.0])
    assert "CODE IS13920" in write_std(frame)
    frame.ductile = False
    assert "CODE INDIAN" in write_std(frame)


def test_id_list_ranges():
    assert _id_list([1, 2, 3, 5, 7, 8]) == "1 TO 3 5 7 TO 8"
    assert _id_list([4]) == "4"


def test_eudl_per_beam_for_is13920_shear():
    frame = make_rc_frame([4.0], [4.0])
    udl = beam_gravity_udl(frame)
    L = frame.loads
    self_wt = 0.45 * 0.23 * 25
    wall = 0.23 * 20 * (3.0 - 0.45)
    floor = 1.2 * (self_wt + wall + (L.floor_dead() + L.floor_live) * 4.0 / 3)
    roof = 1.2 * (self_wt + 0.23 * 20 * 1.0 + (L.floor_dead() + L.roof_live) * 4.0 / 3)
    assert udl[9] == pytest.approx(floor) and udl[13] == pytest.approx(roof)
    text = write_std(frame)
    assert f"EUDL {round(floor, 2):g} MEMB 9 TO 12" in text
    assert f"EUDL {round(roof, 2):g} MEMB 13 TO 16" in text
    assert text.index("EUDL") < text.index("DESIGN BEAM")


def test_output_is_deterministic_and_job_info_valid():
    frame = make_rc_frame([4.0], [4.0])
    text = write_std(frame)
    assert text == write_std(make_rc_frame([4.0], [4.0]))
    job = _section(text, "START JOB INFORMATION", "END JOB INFORMATION")
    assert all(line.split()[0] in {"JOB", "ENGINEER", "CLIENT", "CHECKER"} for line in job)


def test_column_bars_become_minmain_per_group():
    frame = make_rc_frame([4.0], [4.0])
    assert "MINMAIN" not in write_std(frame)
    frame.column_bars = {"column-1": 16, "column-0": 20}
    text = write_std(frame)
    assert "MINMAIN 20 MEMB 1 TO 4\nMINMAIN 16 MEMB 5 TO 8\n" in text
    assert text.index("TRACK 2 ALL") < text.index("MINMAIN") < text.index("DESIGN BEAM")
    assert frame.min_bar("column-1") == 16 and make_rc_frame([4.0], [4.0]).min_bar("column-1") == 12


def test_beam_bars_capped_at_column_size_over_20():
    """IS 13920 cl. 7.1.1(a): 300 mm columns allow 15 mm beam bars -> 12 mm; 350 mm -> 16 mm."""
    frame = make_rc_frame([4.0], [4.0])
    text = write_std(frame)
    assert "MAXMAIN 12 MEMB 9 TO 12\nMAXMAIN 12 MEMB 13 TO 16\n" in text
    frame.sections.update({"column-0": Section(0.45, 0.45), "column-1": Section(0.35, 0.35)})
    assert frame.beam_bar_cap(1) == 16  # floor beams meet both storeys: the smaller 350 column governs
    assert frame.beam_bar_cap(2) == 16
    frame.sections["column-1"] = Section(0.40, 0.40)
    assert frame.beam_bar_cap(1) == 20 and frame.beam_bar_cap(2) == 20
    frame.ductile = False
    assert "MAXMAIN" not in write_std(frame)
