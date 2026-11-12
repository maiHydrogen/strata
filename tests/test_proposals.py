"""LLM layout sketches: schema without open-ended lists, mapping onto the GA genome, retries (scripted LLM, offline)."""

import json

from src.brief.llm import ScriptedClient, model_schema
from src.inference.spec import build_spec
from src.spatial.ga import decode, evaluate, problem
from src.spatial.proposals import genome_from_proposal, propose, sketch_schema
from src.spatial.sample_layout import sample_brief

B = sample_brief()
P = problem(B, build_spec(B))
REPLY = {"bands": 3, "depths_m": {"band1": 3.6, "band2": 3.0, "band3": 5.0, "band4": 0, "band5": 0},
         "passage": "centre", "stair_band": 1, "stair_side": "right", "upper_terrace": "front",
         "floor": {"Bedroom 1": 1, "Bedroom 2": 1, "Bedroom 3": 0, "Dining": 0, "Kitchen": 0, "Common bathroom 1": 0,
                   "Store": 0},
         "position": {"Dining": 1, "Kitchen": 2, "Bedroom 3": 3, "Common bathroom 1": 4, "Store": 5, "Bedroom 1": 6,
                      "Bedroom 2": 7}}


def test_schema_has_no_open_ended_lists_and_names_every_room():
    schema = json.dumps(model_schema(sketch_schema(P)))
    assert '"type": "array"' not in schema  # Gemma looped on lists in testing
    for name in ("Bedroom 1", "Kitchen", "Common bathroom 1", "Store"):
        assert f'"{name}"' in schema


def test_sketch_maps_onto_the_genome_and_decodes_as_asked():
    sketches, log = propose(ScriptedClient([REPLY] + [REPLY] * 3), P, count=1)
    assert len(sketches) == 1 and sketches[0].idea.startswith("bedrooms upstairs")
    plan = decode(genome_from_proposal(sketches[0], P), P)
    assert len(plan.bands) - 1 == 3 and plan.stair == (1, "R")
    where = {r[0]: f for f, cells in enumerate(plan.floors) for c in cells for r in c.rooms}
    assert where["Bedroom 1"] == where["Bedroom 2"] == 1 and where["Bedroom 3"] == 0
    assert plan.extent[1][0] > 0  # front terrace: the upper floor starts behind the front band
    hard, *_ = evaluate(genome_from_proposal(sketches[0], P), P, detail=True)
    assert hard == 0


def test_invalid_reply_is_retried_and_a_failed_focus_is_skipped():
    looped = '{"bands": 3, "floor": {"Bedroom 1": 1, "Bedroom 1": 1, "Bedroom 1"'
    sketches, log = propose(ScriptedClient([looped, json.dumps(REPLY) + "\n```", "x", "x", "x"]), P, count=2)
    assert len(sketches) == 1 and len(log) == 5  # focus 1: bad then good; focus 2: three bad replies
    assert "invalid" in log[1]["prompt"]
