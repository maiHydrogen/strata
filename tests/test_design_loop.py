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


def test_joint_failure_makes_light_beams_shallower_first():
    frame = make_rc_frame([4.0], [4.0], storeys=2)
    new, changes, feasible, deepened = resize(frame, read_anl(FIXTURE))
    assert feasible
    assert new.sections["beam-1"] == Section(0.40, 0.23)
    assert new.sections["beam-2"] == Section(0.40, 0.23)
    assert new.sections["column-0"] == Section(0.30, 0.30)
    assert len(changes) == 2 and all("IS 13920 cl. 7.2" in c and "(practice)" in c for c in changes)
    assert frame.sections["beam-1"] == Section(0.45, 0.23)  # input frame untouched


def test_joint_failure_grows_columns_when_beams_heavily_reinforced():
    frame = make_rc_frame([4.0], [4.0], storeys=2)
    new, changes, feasible, _ = resize(frame, parse_anl(HEAVY_BEAMS))
    assert feasible
    assert new.sections["column-0"] == Section(0.35, 0.35)
    assert new.sections["column-1"] == Section(0.35, 0.35)
    assert new.sections["beam-1"] == Section(0.45, 0.23)


def test_beams_at_span_over_12_are_not_made_shallower():
    frame = make_rc_frame([4.0], [4.0], storeys=2, beam=Section(0.35, 0.23))  # next step 0.30 < 4/12
    new, _, feasible, _ = resize(frame, read_anl(FIXTURE))
    assert feasible
    assert new.sections["beam-1"].depth == 0.35 and new.sections["column-0"] == Section(0.35, 0.35)


def test_deepened_beams_are_never_made_shallower_again():
    frame = make_rc_frame([4.0], [4.0], storeys=2)
    new, _, _, deepened = resize(frame, read_anl(FIXTURE), deepened={"beam-1", "beam-2"})
    assert new.sections["beam-1"].depth == 0.45 and new.sections["column-0"] == Section(0.35, 0.35)
    assert deepened == {"beam-1", "beam-2"}


def test_columns_and_beams_at_limits_is_infeasible():
    frame = make_rc_frame([4.0], [4.0], storeys=2, column=Section(0.50, 0.50), beam=Section(0.35, 0.23))
    _, changes, feasible, _ = resize(frame, read_anl(FIXTURE))
    assert not feasible and "cannot be met" in changes[-1]


def test_nothing_to_do_when_all_pass():
    frame = make_rc_frame([4.0], [4.0])
    new, changes, feasible, _ = resize(frame, parse_anl(PASSING))
    assert changes == [] and feasible


def test_loop_converges_by_shallower_beams_with_stub_staad():
    """Stub STAAD: the joint check passes once beams are 380 mm deep or less."""
    calls = []

    def stub(std_text, name):
        calls.append(name)
        beam = float(re.search(r"^9 TO 16 PRISMATIC YD ([\d.]+)", std_text, re.M).group(1))
        return PASSING if beam <= 0.38 else open(FIXTURE, encoding="latin-1").read()

    outcome = design(make_rc_frame([4.0], [4.0]), stub, name="t")
    assert outcome.passed
    assert calls == ["t_r1", "t_r2", "t_r3"]
    assert [r.sections["beam-1"] for r in outcome.rounds] == ["230x450", "230x400", "230x380"]
    assert outcome.frame.sections["column-0"] == Section(0.30, 0.30)


def test_loop_reports_infeasible():
    always_failing = open(FIXTURE, encoding="latin-1").read()
    outcome = design(make_rc_frame([4.0], [4.0]), lambda t, n: always_failing, name="t", max_rounds=12)
    assert not outcome.passed
    assert "cannot be met" in outcome.reason
    beams = [r.sections["beam-1"] for r in outcome.rounds]
    assert beams[:4] == ["230x450", "230x400", "230x380", "230x350"]  # beams first, down to span/12
    assert outcome.rounds[-1].sections["column-0"] == "500x500"


def test_column_bar_size_error_grows_columns_with_clause():
    frame = make_rc_frame([4.0], [4.0], beam=Section(0.35, 0.23))
    results = read_anl(os.path.join(os.path.dirname(__file__), "fixtures", "frame_g1_col_bar_error.anl"))
    new, changes, feasible, _ = resize(frame, results)
    assert feasible
    assert new.sections["column-0"] == Section(0.35, 0.35) and new.sections["beam-1"].depth == 0.35
    assert all("cl. 7.1.1 a)" in c for c in changes)
