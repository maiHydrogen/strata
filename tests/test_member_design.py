"""Each member's design record carries what Revit shows and the IFC check reads back."""

from types import SimpleNamespace

from src.execution.frame_to_building import _beam_design, _beam_utilisation, _column_design


def test_beam_utilisation_is_the_worse_face():
    b = SimpleNamespace(max_top_required=300.0, max_top_provided=400.0, max_bottom_required=200.0,
                        max_bottom_provided=200.0, min_stirrup_spacing_mm=100, failed=False, errors=[])
    assert _beam_utilisation(b) == 1.0
    d = _beam_design(b)
    assert d["utilisation"] == 1.0 and d["status"] == "pass" and "top 300/400 mm2" in d["steel"]
    b.max_top_provided = b.max_bottom_provided = 0
    assert _beam_utilisation(b) is None


def test_column_design_has_utilisation_and_steel():
    c = SimpleNamespace(errors=[], below_min_steel=False, bars="8-20d", steel_ratio_pct=1.55,
                        interaction_ratio=0.6123, axial_kn=420.0, failed=False)
    d = _column_design(c, 16)
    assert d["utilisation"] == 0.612 and d["steel"] == "8-20d, 1.55% of gross area" and d["status"] == "pass"
    assert _column_design(None, 16)["status"] == "not designed"
