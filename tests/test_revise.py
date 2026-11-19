"""Free-text layout change requests: the LLM returns edits only; rules, search and checks are code."""

import json
from pathlib import Path

import pytest

from src.brief.llm import LLMError, ScriptedClient
from src.brief.schema import Brief
from src.inference.spec import build_spec
from src.spatial.checker import check_layout
from src.spatial.ga import alternatives, decode, evaluate, to_layout
from src.spatial.revise import Edit, apply_edits, check_edits, parse_request, revise, room_names, wish_met

ROOT = Path(__file__).resolve().parents[1]
NONE = dict(kind="none", room="", other="", floor=0, area_m2=0, where="", note="")


@pytest.fixture(scope="module")
def approved():
    b = Brief.model_validate(json.loads((ROOT / "examples/g1_40x60.brief.json").read_text())).with_defaults()
    spec = build_spec(b)
    gs, p = alternatives(b, spec, count=1, restarts=2, generations=100, seed=1)
    return b, spec, gs[0], p


def _reply(*edits):
    out = {f"edit{k}": dict(NONE) for k in range(1, 5)}
    for k, e in enumerate(edits, 1):
        out[f"edit{k}"].update(e)
    return out


def test_reply_becomes_edits_and_none_slots_are_dropped(approved):
    _, _, _, p = approved
    llm = ScriptedClient([_reply({"kind": "adjacent", "room": "Kitchen", "other": "Dining"})])
    edits, log = parse_request(llm, "kitchen by the dining please", p)
    assert edits == [Edit("adjacent", "Kitchen", "Dining")]
    assert "Kitchen" in llm.calls[0]["prompt"] and "kitchen by the dining" in llm.calls[0]["prompt"]


def test_invented_room_names_are_refused_by_the_schema_and_retried(approved):
    _, _, _, p = approved
    bad = _reply({"kind": "bigger", "room": "Home theatre"})
    good = _reply({"kind": "bigger", "room": "Bedroom 2"})
    edits, log = parse_request(ScriptedClient([bad, good]), "bigger bedroom", p)
    assert len(log) == 2 and "previous JSON was invalid" in log[1]["prompt"]
    assert edits[0].room == "Bedroom 2"
    with pytest.raises(LLMError):
        parse_request(ScriptedClient([bad, bad, bad]), "x", p)


def test_rule_breaking_edits_are_refused_with_the_clause(approved):
    _, spec, g, p = approved
    edits = [Edit("smaller", "Kitchen", area_m2=2.0), Edit("move_to_floor", "Living room", floor=1),
             Edit("move_to_floor", "Kitchen", floor=5), Edit("unsupported", note="a skylight"),
             Edit("swap", "Living room", "Dining")]
    ok, no = check_edits(edits, p, g, spec)
    assert ok == [] and len(no) == 5
    assert "Part 3, cl. 12.3.2" in no[0][1]  # the kitchen minimum's clause, from the spec


def test_bigger_without_a_figure_targets_twenty_percent_more(approved):
    _, spec, g, p = approved
    ok, _ = check_edits([Edit("bigger", "Bedroom 1")], p, g, spec)
    plan = decode(g, p)
    now = wish_met(Edit("bigger", "Bedroom 1", area_m2=0.0), g, plan, p)[1]
    assert ok[0].area_m2 == pytest.approx(float(now.split()[0]) * 1.2, abs=0.1)


def test_swap_and_floor_edits_go_into_the_starting_genome(approved):
    _, _, g, p = approved
    q, start = apply_edits(p, g, [Edit("swap", "Bedroom 2", "Bedroom 3"), Edit("move_to_floor", "Store", floor=0)])
    plan = decode(start, q)
    assert wish_met(q.wishes[0], start, plan, q)[0]  # places exchanged in the seed
    assert next(u for u in q.units if u.name == "Store").floor == 0
    assert p.wishes == [] and next(u for u in p.units if u.name == "Store").floor is None  # original untouched


def test_revision_meets_the_wishes_and_passes_the_checker(approved):
    b, spec, g, p = approved
    ok, _ = check_edits([Edit("bigger", "Bedroom 1"), Edit("terrace", where="rear")], p, g, spec)
    genomes, q, report = revise(b, spec, g, ok, p, count=2, restarts=2, generations=80, seed=1)
    assert genomes and all(w["met"] for w in report[0])
    assert evaluate(genomes[0], q, detail=True)[0] == 0
    assert check_layout(to_layout(genomes[0], q), spec)["pass"]


def test_genome_from_another_brief_is_refused(approved):
    b, spec, g, p = approved
    with pytest.raises(ValueError):
        revise(b, spec, g[:-2], [Edit("terrace", where="rear")], p)


def test_room_names_offered_to_the_llm_are_the_layout_units(approved):
    _, _, _, p = approved
    assert room_names(p)[0] == "Living room" and "Kitchen" in room_names(p)


def test_web_layout_revise_action(approved, monkeypatch):
    import importlib.util

    import src.brief.llm as llm_mod
    b, _, _, _ = approved
    spec_ = importlib.util.spec_from_file_location("web_layout", ROOT / "web/api/layout.py")
    web = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(web)
    status, first = web.design({"brief": b.model_dump(mode="json"), "seed": 1})
    assert status == 200 and len(first["options"][0]["genome"]) > 10
    monkeypatch.setattr(llm_mod, "make_client", lambda *a, **k: ScriptedClient(
        [_reply({"kind": "terrace", "where": "rear"}, {"kind": "smaller", "room": "Kitchen", "area_m2": 1.0})]))
    status, out = web.design({"action": "revise", "brief": b.model_dump(mode="json"), "seed": 1,
                              "genome": first["options"][0]["genome"], "request": "terrace at the back, tiny kitchen"})
    assert status == 200 and out["options"][0]["name"].startswith("Revised")
    assert [e["accepted"] for e in out["edits"]] == [True, False]
    assert out["options"][0]["wishes"][0]["met"]


# ---- door edits: "the bath next to bedroom 4 should open into bedroom 4" ----

from src.spatial.ga import Cell, Plan, problem  # noqa: E402
from src.spatial.layout import Door, Floor, Layout, Room  # noqa: E402
from src.spatial.revise import move_doors, neighbours, prompt  # noqa: E402


@pytest.fixture(scope="module")
def three_attached():
    """The courtyard brief with 3 attached and 2 common baths: Bedroom 4 has no bath of its own."""
    d = json.loads((ROOT / "examples/court_60x80.brief.json").read_text())
    d["programme"].update(bathrooms_attached=3, bathrooms_common=2)
    b = Brief.model_validate(d).with_defaults()
    spec = build_spec(b)
    return b, spec, problem(b, spec)


def _plan(*rooms):
    cell = Cell(1, "L", 0.0, 8.0, 0.0, 3.0, rooms=list(rooms))
    return Plan(width=8.0, bands=[0.0, 3.0], spine=3.0, stair=None, floors=[[cell]], extent=[(0.0, 3.0)])


BED4 = ("Bedroom 4", "bedroom", 0.0, 0.0, 3.0, 3.0)
CB1 = ("Common bathroom 1", "bath_wc", 3.0, 0.0, 5.0, 2.0)  # 2.0 m of wall with Bedroom 4
CB2 = ("Common bathroom 2", "bath_wc", 3.0, 2.0, 5.0, 3.0)  # 1.0 m: too short for a door and its frame
BED1 = ("Bedroom 1", "bedroom", 5.0, 0.0, 8.0, 3.0)


def _refusal(p, plan, edit, accepted=()):
    from src.spatial.revise import _door_refusal, _units
    u = _units(p)
    return _door_refusal(edit, u[edit.room], u[edit.other], plan, list(accepted))


def test_a_common_bath_beside_a_bedroom_can_open_into_it(three_attached):
    _, _, p = three_attached
    assert _refusal(p, _plan(BED4, CB1, CB2, BED1), Edit("door", "Common bathroom 1", "Bedroom 4")) == ""
    assert Edit("door", "Common bathroom 1", "Bedroom 4").text() == "Common bathroom 1 opens into Bedroom 4"


def test_door_edits_that_break_a_rule_are_refused_with_the_reason(three_attached):
    _, _, p = three_attached
    plan = _plan(BED4, CB1, CB2, BED1)
    assert "1.00 m of wall" in _refusal(p, plan, Edit("door", "Common bathroom 2", "Bedroom 4"))
    assert "entered only through" in _refusal(p, plan, Edit("door", "Bedroom 1", "Common bathroom 1"))
    assert "reached only through" in _refusal(p, plan, Edit("door", "Bedroom 1", "Bedroom 4"))
    assert "main entrance" in _refusal(p, plan, Edit("door", "Living room", "Bedroom 4"))
    # the last common bath on the floor stays common while a bedroom there has no bath of its own
    alone = _plan(BED4, CB1, BED1)
    assert "Bedroom 4 would have no bathroom" in _refusal(p, alone, Edit("door", "Common bathroom 1", "Bedroom 1"))
    far = _plan(BED4, ("Common bathroom 1", "bath_wc", 6.0, 0.0, 8.0, 2.0))
    assert "do not share a wall" in _refusal(p, far, Edit("door", "Common bathroom 1", "Bedroom 4"))


def test_the_prompt_names_each_rooms_neighbours(three_attached):
    _, _, p = three_attached
    text = prompt("the bath next to bedroom 4 should open into it", p, _plan(BED4, CB1, CB2, BED1))
    assert "Common bathroom 1 (floor 0): next to Bedroom 4, Common bathroom 2, Bedroom 1" in text
    assert "Common bathroom 1" in neighbours(_plan(BED4, CB1))[0]


def test_move_doors_puts_the_door_on_the_shared_wall_and_keeps_inner_baths():
    sq = lambda a, b, c, d: [(a, b), (c, b), (c, d), (a, d)]  # noqa: E731
    rooms = [Room(id="bedroom4", type="bedroom", name="Bedroom 4", polygon=sq(0, 0, 3, 3)),
             Room(id="bath_wc1", type="bath_wc", name="Common bathroom 1", polygon=sq(3, 0, 5, 2)),
             Room(id="living", type="living", name="Living room", polygon=sq(3, 2, 8, 6))]
    doors = [Door(id="D_bath_wc1", between=("bath_wc1", "living"), at=(4.0, 2.0), width=0.75),
             Door(id="D_main", between=("living", "outside"), at=(5.5, 6.0), width=1.0, main=True)]
    lay = Layout(name="t", plot={"width": 10, "depth": 10},
                 floors=[Floor(level=0, rooms=rooms, doors=doors)])
    move_doors(lay, [Edit("door", "Common bathroom 1", "Bedroom 4")])
    d = {x.id: x for x in lay.floors[0].doors}
    assert set(d) == {"D_main", "D_bath_wc1"}
    assert d["D_bath_wc1"].between == ("bath_wc1", "bedroom4") and d["D_bath_wc1"].at == (3.0, 1.0)
    assert d["D_bath_wc1"].width == 0.75


def test_a_door_edit_survives_the_redesign_and_passes_the_checker(three_attached):
    b, spec, _ = three_attached
    gs, p = alternatives(b, spec, count=3, restarts=3, generations=100, seed=1)
    for g in gs:
        plan = decode(g, p)
        rooms = [r for c in plan.floors[0] for r in c.rooms]
        pair = next(((r[0], o[0]) for r in rooms if r[0].startswith("Common bathroom") for o in rooms
                     if o[1] == "bedroom" and f"{r[0]} (floor 0): next to" in "\n".join(neighbours(plan))
                     and o[0] in next(l for l in neighbours(plan) if l.startswith(r[0] + " ("))), None)
        if pair is None:
            continue
        ok, no = check_edits([Edit("door", *pair)], p, g, spec)
        if not ok:
            continue
        new, q, report = revise(b, spec, g, ok, p, count=1, seed=1)
        lay = to_layout(new[0], q)
        ids = {r.name: r.id for r in lay.floors[0].rooms}
        door = next(d for d in lay.floors[0].doors if ids[pair[0]] in d.between)
        assert report[0][0]["met"] and set(door.between) == {ids[pair[0]], ids[pair[1]]}
        assert sum(ids[pair[0]] in d.between for d in lay.floors[0].doors) == 1
        failed = [r for r in check_layout(lay, spec)["results"] if r["pass"] is False and r["basis"] != "practice"]
        assert not [r for r in failed if r["category"] in ("doors", "circulation")], failed
        return
    pytest.skip("no option with a common bath beside a bedroom at this seed")
