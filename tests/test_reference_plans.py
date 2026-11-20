"""Hand-drawn Indian plot reference plans pass the rule checker with no failure or warning."""

import pytest

from src.inference.spec import build_spec
from src.spatial.checker import check_layout
from src.spatial.reference_plans import PLANS


@pytest.mark.parametrize("name", sorted(PLANS))
def test_reference_plan_passes_every_check(name):
    brief, layout = PLANS[name]()
    r = check_layout(layout, build_spec(brief))
    bad = [x for x in r["results"] if x["pass"] is False]
    assert r["pass"] and not bad, bad
    assert len(layout.floors) == brief.floors_now
    assert sum(room.type == "bedroom" for f in layout.floors for room in f.rooms) == brief.programme.bedrooms
