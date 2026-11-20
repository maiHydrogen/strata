"""The structure code's fixed numbers stay within the IS 456 / IS 13920 values in the knowledge graph."""

import pytest

from src.inference.spec import build_spec
from src.spatial.sample_layout import sample_brief
from src.structure import footings, sizing
from src.structure.from_layout import member_value
from src.structure.staad_results import MIN_COLUMN_STEEL_PCT
from src.structure.std_writer import DEFAULT_COLUMN_BAR, Frame


@pytest.fixture(scope="module")
def spec():
    return build_spec(sample_brief())


def rule(spec, element, quantity, comparator=None):
    v = member_value(spec, element, quantity, None, comparator)
    assert v is not None, f"{element}.{quantity} missing from the spec"
    return v


def test_member_rules_reach_the_spec_with_clauses(spec):
    got = {(e["element"], e["quantity"], e["comparator"]) for e in spec["structure"] if e["element"] != "floor"}
    assert ("column", "width", "ge") in got and ("beam", "span_depth_ratio", "le") in got
    assert all(e["source"]["clause"] for e in spec["structure"])


def test_code_constants_meet_the_graph(spec):
    assert min(s.width for s in sizing.COLUMN_SIZES) >= rule(spec, "column", "width")
    assert sizing.BEAM_WIDTH >= rule(spec, "beam", "width")
    assert sizing.BEAM_WIDTH / max(sizing.BEAM_DEPTHS) >= rule(spec, "beam", "width_depth_ratio")
    assert DEFAULT_COLUMN_BAR / 1000 >= rule(spec, "column", "bar_diameter")
    assert MIN_COLUMN_STEEL_PCT / 100 == rule(spec, "column", "steel_ratio", "ge")
    assert sizing.MAX_COLUMN_STEEL_PCT / 100 <= rule(spec, "column", "steel_ratio", "le")
    assert sizing.MAX_BEAM_STEEL_PCT / 100 <= rule(spec, "beam", "steel_ratio", "le")
    assert sizing.SLAB_SPAN_DEPTH == rule(spec, "slab", "span_depth_ratio")
    assert footings.COVER >= rule(spec, "footing", "cover")
    f = Frame()
    assert f.beam_cover >= rule(spec, "beam", "cover") and f.column_cover >= rule(spec, "column", "cover")
