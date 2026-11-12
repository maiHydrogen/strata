"""Layout GA: decoding is always well formed, the GA finds checker-passing layouts, narrow plots report what does not fit."""

import json
import random
from pathlib import Path

import pytest
from shapely.geometry import box
from shapely.ops import unary_union

from src.brief.schema import Brief
from src.inference.spec import build_spec
from src.spatial.checker import check_layout
from src.spatial.ga import decode, evaluate, genome_length, problem, run, stair_design, suggest, to_layout
from src.spatial.sample_layout import sample_brief

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def sample():
    b = sample_brief()
    return b, build_spec(b)


def test_random_genomes_decode_to_well_formed_layouts(sample):
    b, spec = sample
    p = problem(b, spec)
    rng = random.Random(7)
    x0, y0, w, d = p.envelope
    envelope = box(x0, y0, x0 + w, y0 + d).buffer(0.01)
    for _ in range(25):
        g = [rng.random() for _ in range(genome_length(p))]
        lay = to_layout(g, p)
        stairs = []
        for f in lay.floors:
            shapes = [r.shape for r in f.rooms]
            assert unary_union(shapes).within(envelope)
            assert sum(a.intersection(c).area for i, a in enumerate(shapes) for c in shapes[i + 1:]) < 1e-6
            stairs.append(next(r.shape for r in f.rooms if r.type == "stair"))
        assert all(s.equals(stairs[0]) for s in stairs)  # the stair is in the same place on every floor
        ground = {c.at for c in lay.floors[0].columns}
        assert all({c.at for c in f.columns} <= ground for f in lay.floors[1:])  # upper columns stand on lower ones


def test_ga_finds_a_layout_the_independent_checker_passes(sample):
    b, spec = sample
    hof, p, history = run(b, spec, pop_size=80, generations=60, seed=1, restarts=2)
    hard, soft, why, plan = evaluate(hof[0], p, detail=True)
    assert hard == 0 and why == []
    assert history[-1] <= history[0]
    result = check_layout(to_layout(hof[0], p), spec)
    assert result["pass"], [r for r in result["results"] if r["pass"] is False]
    for floor in to_layout(hof[0], p).floors:  # no bedroom has to take the stairs to reach a bath
        names = [r.name for r in floor.rooms]
        lonely = [n for n in names if n.startswith("Bedroom") and f"Bath of {n}" not in names]
        assert not lonely or any(n.startswith("Common bathroom") for n in names)


def test_narrow_plot_uses_a_side_spine_and_reports_what_does_not_fit():
    b = Brief.model_validate(json.load(open(ROOT / "examples" / "g1_house.brief.json"))).with_defaults()
    spec = build_spec(b)  # 30 x 50 ft plot: 5.5 m wide after setbacks
    hof, p, _ = run(b, spec, pop_size=60, generations=40, seed=1)
    hard, _, why, plan = evaluate(hof[0], p, detail=True)
    assert plan.single and hard > 0 and any("did not fit" in w for w in why)
    result = check_layout(to_layout(hof[0], p), spec)
    corridor = [r for r in result["results"] if r["category"] == "rooms" and "corridor" in r["subject"]]
    assert corridor and all(r["pass"] for r in corridor)  # the side passage keeps 1.0 m clear
    # 54 m2 a floor cannot hold this G+1 programme at NBC minimums; the GA says which changes would
    options = suggest(b, build_spec, restarts=2, generations=80, seed=1)
    fitting = [o["change"] for o in options if o["fits"]]
    assert fitting and options[0]["fits"]
    assert any("one more storey" in c for c in fitting)


def test_stair_design_meets_the_nbc_limits():
    s = stair_design(3.0)
    assert s.riser <= 0.19 and s.risers_per_flight * 2 * s.riser == pytest.approx(3.0, abs=0.01)
    assert s.risers_per_flight <= 12 and s.tread >= 0.25
