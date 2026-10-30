"""Tests for the STAAD .anl parser, against a real STAAD.Pro 2026 run of the spike frame."""

import os

import pytest

from src.structure.staad_results import JointCheck, parse_anl, parse_joint_table, read_anl
from src.structure.staad_runner import read_report

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "frame_g1.anl")


@pytest.fixture(scope="module")
def res():
    return read_anl(FIXTURE)


def test_version_and_no_errors(res):
    assert res.version == "26.00.00.340"
    assert res.errors == []


def test_seismic_base_shear_matches_hand_calc(res):
    # Ah = Z/2 * I/R * Sa/g = 0.18 * 1/5 * 2.5 = 0.09; W = DL 530.08 + 0.25 * 2.0 * 16 m2 = 538.08 kN
    for d in ("X", "Z"):
        assert res.base_shear[d]["coeff"] == pytest.approx(0.09)
        assert res.base_shear[d]["weight"] == pytest.approx(538.08)
        assert res.base_shear[d]["vb"] == pytest.approx(48.43)
        assert res.base_shear[d]["period"] == pytest.approx(0.075 * 6 ** 0.75, abs=1e-4)


def test_all_members_parsed(res):
    assert sorted(res.beams) == list(range(9, 17))
    assert sorted(res.columns) == list(range(1, 9))


def test_beam_9_values(res):
    b = res.beams[9]
    assert (b.width_mm, b.depth_mm) == (230.0, 450.0)
    assert b.max_top_required == pytest.approx(417.16)
    assert b.max_top_provided == pytest.approx(452.39)
    assert b.max_bottom_required == pytest.approx(228.53)
    assert b.min_stirrup_spacing_mm == 70
    assert not b.failed


def test_column_1_values(res):
    c = res.columns[1]
    assert (c.width_mm, c.depth_mm) == (300.0, 300.0)
    assert c.axial_kn == pytest.approx(219.53)
    assert c.steel_required == pytest.approx(567.03)
    assert c.bars == "4-16d" and c.steel_ratio_pct == pytest.approx(0.89)
    assert c.interaction_ratio == pytest.approx(0.78)
    assert not c.failed


def test_joint_check_failure_blocks_pass(res):
    assert res.joint_strength_failed
    assert not res.all_passed
    assert any("UDL LOADING IS NOT DEFINED" in w for w in res.warnings)


def test_column_over_capacity_is_failed():
    text = open(FIXTURE, encoding="latin-1").read().replace("IR: 0.78", "IR: 1.12", 1)
    assert parse_anl(text).columns[1].failed


def test_eudl_run_clears_udl_warnings_and_raises_capacity_shear():
    eudl = read_anl(os.path.join(os.path.dirname(__file__), "fixtures", "frame_g1_eudl.anl"))
    assert eudl.errors == []
    assert not any("UDL LOADING IS NOT DEFINED" in w for w in eudl.warnings)
    assert eudl.joint_strength_failed  # strong-column/weak-beam still fails: needs the resize loop
    text = open(os.path.join(os.path.dirname(__file__), "fixtures", "frame_g1_eudl.anl"), encoding="latin-1").read()
    assert "DESIGN SHEAR FORCE AT SECTION     0.0 IS   110.40 KN" in text


def test_bare_error_lines_fail_their_columns():
    """STAAD 2026 prints 'ERROR:' without '***'; IS 13920 cl. 7.1.1(a) column size check."""
    r = read_anl(os.path.join(os.path.dirname(__file__), "fixtures", "frame_g1_col_bar_error.anl"))
    assert len(r.errors) == 8
    assert all("7.1.1 a)" in e for e in r.errors)
    assert sorted(r.columns) == list(range(1, 9))
    assert all(c.failed and len(c.errors) == 1 for c in r.columns.values())
    assert not r.all_passed


def test_members_missing_from_results_count_as_failed():
    text = open(FIXTURE, encoding="latin-1").read()
    start = text.index("C O L U M N   N O.      8")
    end = text.index("END OF COLUMN DESIGN RESULTS")
    r = parse_anl(text[:start] + text[end:])
    assert r.columns[8].failed and r.columns[8].width_mm == 0.0


def test_errors_inside_a_member_block_belong_to_that_member():
    """G+1 house round 3: STAAD's over-reinforced error names no member, only its position does."""
    r = read_anl(os.path.join(os.path.dirname(__file__), "fixtures", "g1_house_overreinforced.anl"))
    assert r.general_errors == []
    failed = {i for i, b in r.beams.items() if b.failed}
    assert failed == {21, 22}
    assert all("OVER-REINFORCED" in r.beams[i].errors[0] for i in failed)


FIXTURES = os.path.dirname(FIXTURE)


def test_joint_report_rows_and_the_failing_roof_joint():
    """G+1 house round 8: STAAD's <name>_13920.txt lists both ends of all 18 columns."""
    r = read_report(FIXTURES, "g1_house_r8")
    res = parse_anl(r)
    assert len(res.joints) == 36
    assert res.joints[1] == JointCheck(1, 10, 10, (19,), (25,), "P", "P")
    assert res.failed_joints == [JointCheck(14, 23, 0, (33, 34), (39, 40), "F", "P")]
    j = res.failed_joints[0]
    assert j.failed_directions == "X" and j.failed_beams == (33, 34)
    assert res.joint_strength_failed and not res.all_passed


def test_anl_alone_still_flags_the_joint_failure():
    res = read_anl(os.path.join(FIXTURES, "g1_house_r8.anl"))
    assert res.joints == [] and res.joint_strength_failed


def test_track4_anl_table_lists_failing_rows_only():
    text = """
   RELATIVE STRENGTHS OF BEAMS AND COLUMNS AT A JOINT
   ==================================================

     COLUMN     JOINT   CONNECTED COLUMN   CONNECTED             BEAM       STATUS
     -------    -----   ----------------   --------------------------     ----------

                                             X                  Z          X      Z

   *** WARNING: Relative strength checks have failed at beam/column joints
                as per Clause 7.2 of IS-13920:2016.
        14         23           -       33       34        39      40      F      P
   167. FINISH
"""
    assert parse_joint_table(text) == [JointCheck(14, 23, 0, (33, 34), (39, 40), "F", "P")]


def test_passing_joint_report_clears_the_joint_check():
    table = open(os.path.join(FIXTURES, "g1_house_r8_13920.txt"), encoding="latin-1").read()
    res = parse_anl(table.replace("F      P", "P      P"))
    assert len(res.joints) == 36 and res.failed_joints == [] and not res.joint_strength_failed


def test_column_steel_below_0_8_pct_of_gross_fails(res):
    """IS 456 cl. 26.5.3.1(a); STAAD puts its 0.8% on the concrete area required, not the gross area."""
    r8 = read_anl(os.path.join(FIXTURES, "g1_house_r8.anl"))
    assert {c.steel_ratio_pct for c in r8.columns.values()} == {0.36}
    assert all(c.below_min_steel and not c.failed for c in r8.columns.values())
    assert not any(c.below_min_steel for c in res.columns.values())  # 300 mm columns at 0.89% and 1.4%
    text = open(os.path.join(FIXTURES, "g1_house_r8.anl"), encoding="latin-1").read()
    assert not parse_anl(text.replace("(0.36%,", "(0.80%,")).columns[1].below_min_steel


def test_report_without_joint_file_is_the_anl(tmp_path):
    (tmp_path / "x.anl").write_text("ANL TEXT", encoding="latin-1")
    assert read_report(str(tmp_path), "x") == "ANL TEXT"
    (tmp_path / "x_13920.txt").write_text("JOINTS", encoding="latin-1")
    assert read_report(str(tmp_path), "x") == "ANL TEXT\nJOINTS"


def test_runner_starts_staad_hidden_so_no_done_click(tmp_path, monkeypatch):
    from src.structure import staad_runner
    engine = tmp_path / "SProStaad.exe"
    engine.write_text("")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        (tmp_path / "x.anl").write_text("ANL TEXT", encoding="latin-1")
        return staad_runner.subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(staad_runner, "_windows_path", lambda p: "C:\\STRATA\\x.std")
    monkeypatch.setattr(staad_runner.subprocess, "run", fake_run)
    out = staad_runner.StaadRunner(engine=str(engine), workdir=str(tmp_path))("STAAD SPACE\n", "x")
    assert calls == [[str(engine), "STAAD", "C:\\STRATA\\x.std", "/h"]]
    assert out == "ANL TEXT"
