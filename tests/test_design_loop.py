"""Tests for member-group sizing and the STAAD design loop (STAAD replaced by a stub)."""

import os
import re

from src.structure.design_loop import design
from src.structure.sizing import resize
from src.structure.staad_results import parse_anl, read_anl
from src.structure.std_writer import Section, make_rc_frame, write_std

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "frame_g1_eudl.anl")
PASSING = open(FIXTURE, encoding="latin-1").read().replace(
    "*** WARNING: Relative strength checks have failed at beam/column joints", "")


def test_sections_are_grouped_per_storey_and_level():
    frame = make_rc_frame([4.0], [4.0], storeys=2)
    assert sorted(frame.sections) == ["beam-1", "beam-2", "column-0", "column-1"]
    frame.sections["column-0"] = Section(0.35, 0.35)
    text = write_std(frame)
    assert "1 TO 4 PRISMATIC YD 0.35 ZD 0.35" in text
    assert "5 TO 8 PRISMATIC YD 0.3 ZD 0.3" in text


def test_deeper_roof_beam_shortens_wall_below_it():
    frame = make_rc_frame([4.0], [4.0], storeys=2)
    frame.sections["beam-2"] = Section(0.60, 0.23)
    text = write_std(frame)
    assert f"9 TO 12 UNI GY -{0.23 * 20 * (3.0 - 0.60):.3g}" in text


HEAVY_BEAMS = open(FIXTURE, encoding="latin-1").read().replace(
    "228.53/ 339.29( 3-12d )", "1200.00/1256.64( 4-20d )")  # ~1.16% steel in every beam
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
MAX_BARS = {"column-0": 25, "column-1": 25}

# STAAD's IS 13920 joint report for the one-bay frame: only the roof joint of column 5 fails, in X
ROOF_JOINT_REPORT = """
   RELATIVE STRENGTHS OF BEAMS AND COLUMNS AT A JOINT
   ==================================================

     COLUMN     JOINT   CONNECTED COLUMN   CONNECTED             BEAM       STATUS
     -------    -----   ----------------   --------------------------     ----------

                                             X                  Z          X      Z
         1          1           -        -        -         -       -      -      -
                    5           5        9        -        11       -      P      P
         5          5           1        9        -        11       -      P      P
                    9           -       13        -        15       -      F      P
"""


def _report(name):
    return (open(os.path.join(FIXTURES, f"{name}.anl"), encoding="latin-1").read() + "\n"
            + open(os.path.join(FIXTURES, f"{name}_13920.txt"), encoding="latin-1").read())


def test_joint_failure_without_joint_report_adds_column_bars_first():
    frame = make_rc_frame([4.0], [4.0], storeys=2)
    new, changes, feasible, _ = resize(frame, read_anl(FIXTURE))
    assert feasible
    assert new.column_bars == {"column-0": 20, "column-1": 25}  # one step above STAAD's 16 and 20 mm bars
    assert new.sections == frame.sections
    assert changes[0].startswith("column-0: bars 16d -> 20d min (IS 13920 cl. 7.2.1")
    assert changes[1].startswith("column-1: bars 20d -> 25d min (IS 13920 cl. 7.2.1")
    assert all("no joint report" in c for c in changes)
    assert frame.column_bars == {}  # input frame untouched


def test_light_beams_made_shallower_once_bars_run_out():
    frame = make_rc_frame([4.0], [4.0], storeys=2)
    frame.column_bars = dict(MAX_BARS)
    new, changes, feasible, _ = resize(frame, read_anl(FIXTURE))
    assert feasible
    assert new.sections["beam-1"] == Section(0.40, 0.23) and new.sections["beam-2"] == Section(0.40, 0.23)
    assert new.sections["column-0"] == Section(0.30, 0.30) and new.sections["column-1"] == Section(0.30, 0.30)
    assert len(changes) == 2 and all("(practice)" in c for c in changes)


def test_columns_grow_once_beams_cannot_go_shallower():
    frame = make_rc_frame([4.0], [4.0], storeys=2, beam=Section(0.35, 0.23))
    frame.column_bars = dict(MAX_BARS)  # next beam step 0.30 < 4/12
    new, _, feasible, _ = resize(frame, read_anl(FIXTURE))
    assert feasible
    assert new.sections["column-0"] == Section(0.35, 0.35) and new.sections["column-1"] == Section(0.35, 0.35)
    assert new.sections["beam-1"] == Section(0.35, 0.23)


def test_heavily_reinforced_beams_are_not_made_shallower():
    frame = make_rc_frame([4.0], [4.0], storeys=2, column=Section(0.50, 0.50))
    frame.column_bars = dict(MAX_BARS)
    _, changes, feasible, _ = resize(frame, parse_anl(HEAVY_BEAMS))
    assert not feasible and "cannot be met" in changes[-1]


def test_beams_at_span_over_12_are_not_made_shallower():
    frame = make_rc_frame([4.0], [4.0], storeys=2, column=Section(0.50, 0.50), beam=Section(0.35, 0.23))
    frame.column_bars = dict(MAX_BARS)  # next beam step 0.30 < 4/12
    _, changes, feasible, _ = resize(frame, read_anl(FIXTURE))
    assert not feasible and "cannot be met" in changes[-1]


def test_deepened_beams_are_never_made_shallower_again():
    frame = make_rc_frame([4.0], [4.0], storeys=2, column=Section(0.50, 0.50))
    frame.column_bars = dict(MAX_BARS)
    new, _, feasible, deepened = resize(frame, read_anl(FIXTURE), deepened={"beam-1", "beam-2"})
    assert not feasible and new.sections["beam-1"].depth == 0.45
    assert deepened == {"beam-1", "beam-2"}


def test_growing_roof_columns_grows_the_columns_below():
    """A roof joint can only be fixed by the column below it; the ground storey follows (practice)."""
    frame = make_rc_frame([4.0], [4.0], beam=Section(0.35, 0.23))
    frame.column_bars = dict(MAX_BARS)
    results = parse_anl(open(FIXTURE, encoding="latin-1").read() + ROOF_JOINT_REPORT)
    new, changes, feasible, _ = resize(frame, results)
    assert feasible
    assert new.sections["column-1"] == Section(0.35, 0.35) and new.sections["column-0"] == Section(0.35, 0.35)
    assert changes[-1] == "column-0: 300x300 -> 350x350 (practice: not smaller than column-1 above it)"


def test_lower_columns_never_left_smaller_than_upper():
    frame = make_rc_frame([4.0], [4.0])
    frame.sections["column-1"] = Section(0.45, 0.45)
    new, changes, feasible, _ = resize(frame, parse_anl(PASSING))
    assert feasible and new.sections["column-0"] == Section(0.45, 0.45)
    assert changes == ["column-0: 300x300 -> 450x450 (practice: not smaller than column-1 above it)"]


def test_only_columns_at_failing_joints_change():
    results = parse_anl(open(FIXTURE, encoding="latin-1").read() + ROOF_JOINT_REPORT)
    new, changes, feasible, _ = resize(make_rc_frame([4.0], [4.0]), results)
    assert feasible
    assert new.column_bars == {"column-1": 25}
    assert changes == ["column-1: bars 20d -> 25d min (IS 13920 cl. 7.2.1, sum Mc < 1.4 sum Mb at joint 9 X)"]


def test_only_beams_at_failing_joints_are_made_shallower():
    frame = make_rc_frame([4.0], [4.0], column=Section(0.50, 0.50))
    frame.column_bars = dict(MAX_BARS)
    results = parse_anl(open(FIXTURE, encoding="latin-1").read() + ROOF_JOINT_REPORT)
    new, _, feasible, _ = resize(frame, results)
    assert feasible
    assert new.sections["beam-2"] == Section(0.40, 0.23) and new.sections["beam-1"] == Section(0.45, 0.23)


def test_columns_below_min_steel_get_bigger_bars_not_bigger_sections():
    """G+1 house round 8: 500 mm columns with 8-12d (0.36%) fail IS 456 cl. 26.5.3.1(a) on gross area."""
    from src.execution.sample_house import house_frame
    frame = house_frame()
    frame.sections.update({"column-0": Section(0.50, 0.50), "column-1": Section(0.50, 0.50),
                           "beam-1": Section(0.45, 0.23), "beam-2": Section(0.35, 0.23)})
    new, changes, feasible, _ = resize(frame, parse_anl(_report("g1_house_r8")))
    assert feasible
    assert new.column_bars == {"column-0": 16, "column-1": 16}
    assert new.sections == frame.sections
    assert len(changes) == 2 and all("IS 456 cl. 26.5.3.1(a)" in c and "0.36%" in c for c in changes)


def test_nothing_to_do_when_all_pass():
    frame = make_rc_frame([4.0], [4.0])
    new, changes, feasible, _ = resize(frame, parse_anl(PASSING))
    assert changes == [] and feasible


def test_loop_converges_by_bigger_column_bars_with_stub_staad():
    """Stub STAAD: the joint check passes once the ground columns have 20 mm bars."""
    calls = []

    def stub(std_text, name):
        calls.append(name)
        return PASSING if "MINMAIN 20 MEMB 1 TO 4" in std_text else open(FIXTURE, encoding="latin-1").read()

    outcome = design(make_rc_frame([4.0], [4.0]), stub, name="t")
    assert outcome.passed
    assert calls == ["t_r1", "t_r2"]
    assert [r.bars for r in outcome.rounds] == [{"column-0": 12, "column-1": 12}, {"column-0": 20, "column-1": 25}]
    assert outcome.frame.sections == make_rc_frame([4.0], [4.0]).sections


def test_loop_reports_infeasible():
    """Bars up to 25 mm, then beams down to span/12, then sections 300 -> 500, then give up."""
    always_failing = open(FIXTURE, encoding="latin-1").read()
    outcome = design(make_rc_frame([4.0], [4.0]), lambda t, n: always_failing, name="t", max_rounds=12)
    assert not outcome.passed
    assert "cannot be met" in outcome.reason
    assert [r.bars["column-0"] for r in outcome.rounds[:3]] == [12, 20, 25]  # STAAD already gives 16 mm bars
    assert [r.sections["beam-1"] for r in outcome.rounds[1:5]] == ["230x450", "230x400", "230x380", "230x350"]
    assert [r.sections["column-0"] for r in outcome.rounds[4:]] == [
        "300x300", "350x350", "400x400", "450x450", "500x500"]
    assert all(r.sections["column-0"] == r.sections["column-1"] for r in outcome.rounds)


def test_column_bar_size_error_grows_columns_with_clause():
    frame = make_rc_frame([4.0], [4.0], beam=Section(0.35, 0.23))
    results = read_anl(os.path.join(os.path.dirname(__file__), "fixtures", "frame_g1_col_bar_error.anl"))
    new, changes, feasible, _ = resize(frame, results)
    assert feasible
    assert new.sections["column-0"] == Section(0.35, 0.35) and new.sections["beam-1"].depth == 0.35
    assert all("cl. 7.1.1 a)" in c for c in changes)


def test_over_reinforced_beams_are_deepened_not_infeasible():
    from src.execution.sample_house import house_frame
    frame = house_frame()
    frame.sections.update({"column-0": Section(0.35, 0.35), "column-1": Section(0.35, 0.35),
                           "beam-1": Section(0.40, 0.23), "beam-2": Section(0.40, 0.23)})
    results = read_anl(os.path.join(os.path.dirname(__file__), "fixtures", "g1_house_overreinforced.anl"))
    new, changes, feasible, deepened = resize(frame, results)
    assert feasible
    group = frame.group_of(21)
    assert new.sections[group].depth == 0.45 and group in deepened
    assert any("OVER-REINFORCED" in c for c in changes)
