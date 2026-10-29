"""Tests for the STAAD .anl parser, against a real STAAD.Pro 2026 run of the spike frame."""

import os

import pytest

from src.structure.staad_results import parse_anl, read_anl

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
