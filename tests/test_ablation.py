"""Phase 9 ablation: gold briefs, the IFC reader for arm A, arm B's layout conversion and the scorer."""

import json

import pytest

from src.brief.llm import ScriptedClient
from src.evaluation.ablation import score_layout, summarise, table_markdown
from src.evaluation.baseline import extract_code
from src.evaluation.gold_briefs import brief_text, gold_brief, gold_cases, gold_facts
from src.evaluation.ifc_layout import ifc_to_layout, room_type
from src.evaluation.llm_layout import draw, prompt
from src.inference.spec import build_spec
from src.spatial.reference_plans import plan_30x40_g1


def test_gold_briefs_cover_every_case_with_a_plot():
    cases = gold_cases()
    assert len(cases) == 25
    for c, b in cases:
        f = gold_facts(c)
        assert b.site.plot.width == pytest.approx(f["plot_width"])
        assert b.programme.bedrooms == f.get("bedrooms", b.programme.bedrooms)
        assert not b.missing_must() or all(p in ("site.north", "site.roads", "family") for p, _ in b.missing_must())


def test_gold_brief_takes_answers_and_defaults():
    case = {"id": "x", "message": "House please.", "stated": {"plot_width": 9.144, "plot_depth": 12.192},
            "answers": {"programme": {"text": "Two bedrooms.", "facts": {"bedrooms": 2}},
                        "site.north": {"text": "Not sure.", "facts": {}}}}
    b = gold_brief(case)
    assert b.programme.bedrooms == 2 and b.programme.bathrooms_attached == 2  # A5: one per floor until said
    assert b.sources["programme"] == "client" and b.sources["floors_now"].startswith("default")
    assert brief_text(case) == "House please. Two bedrooms."  # an "I don't know" adds nothing


def test_baseline_code_extraction():
    assert extract_code("Here:\n```python\nx = 1\n```\nbye") == "x = 1"
    assert extract_code("x = 2") == "x = 2"


def test_room_types_from_names():
    assert [room_type(n) for n in ("Master Bedroom", "Toilet 1", "Drawing Hall", "Pooja", "Staircase", "Foo")] == \
        ["bedroom", "bath_wc", "living", "puja", "stair", None]


def _ifc(tmp_path):
    """A two-room IFC written the way the baseline's IfcBuilder writes one."""
    ifcopenshell = pytest.importorskip("ifcopenshell")
    import ifcopenshell.api as api
    import numpy as np

    m = ifcopenshell.file(schema="IFC4")
    project = api.run("root.create_entity", m, ifc_class="IfcProject", name="P")
    api.run("unit.assign_unit", m, units=[api.run("unit.add_si_unit", m, unit_type="LENGTHUNIT")])  # metres
    ctx = api.run("context.add_context", m, context_type="Model")
    body = api.run("context.add_context", m, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW",
                   parent=ctx)
    storey = api.run("root.create_entity", m, ifc_class="IfcBuildingStorey", name="Ground")
    storey.Elevation = 0.0
    api.run("aggregate.assign_object", m, relating_object=project, products=[storey])

    def space(name, pts):
        sp = api.run("root.create_entity", m, ifc_class="IfcSpace", name=name)
        sp.LongName = name
        api.run("geometry.edit_object_placement", m, product=sp, matrix=np.eye(4))
        ring = [m.createIfcCartesianPoint(p) for p in pts + [pts[0]]]
        prof = m.createIfcArbitraryClosedProfileDef("AREA", None, m.createIfcPolyline(ring))
        solid = m.createIfcExtrudedAreaSolid(prof, m.createIfcAxis2Placement3D(m.createIfcCartesianPoint((0., 0., 0.))),
                                             m.createIfcDirection((0., 0., 1.)), 3.0)
        sp.Representation = m.createIfcProductDefinitionShape(None, None, [
            m.createIfcShapeRepresentation(body, "Body", "SweptSolid", [solid])])
        api.run("aggregate.assign_object", m, relating_object=storey, products=[sp])

    space("Living Room", [(2.0, 4.0), (6.0, 4.0), (6.0, 8.0), (2.0, 8.0)])
    space("Bedroom", [(2.0, 8.0), (6.0, 8.0), (6.0, 11.0), (2.0, 11.0)])

    def door(name, x, y, width):
        d = api.run("root.create_entity", m, ifc_class="IfcDoor", name=name)
        mat = np.eye(4)
        mat[0, 3], mat[1, 3] = x, y
        api.run("geometry.edit_object_placement", m, product=d, matrix=mat)
        d.OverallWidth, d.OverallHeight = width, 2.1
        api.run("spatial.assign_container", m, relating_structure=storey, products=[d])

    door("Main", 3.5, 4.0, 1.0)  # centre (4.0, 4.0): front wall of the living room
    door("Inner", 3.55, 8.0, 0.9)  # centre (4.0, 8.0): shared wall
    path = tmp_path / "a.ifc"
    m.write(str(path))
    return path


def test_ifc_reader_gives_rooms_and_doors(tmp_path):
    layout, notes = ifc_to_layout(_ifc(tmp_path), "t", {"width": 9.0, "depth": 15.0})
    f = layout.floors[0]
    assert sorted((r.id, r.type) for r in f.rooms) == [("bedroom", "bedroom"), ("living_room", "living")]
    doors = {d.between: d for d in f.doors}
    assert doors[("living_room", "outside")].main and doors[("living_room", "outside")].at == (4.0, 4.0)
    assert set(doors) - {("living_room", "outside")} in ({("living_room", "bedroom")}, {("bedroom", "living_room")})


def test_score_layout_on_a_reference_plan():
    brief, layout = plan_30x40_g1()
    s, check = score_layout(layout, build_spec(brief))
    assert s["pass"] and s["reachable"] and s["programme"] and s["n_failures"] == 0
    assert s["code"][0] == s["code"][1] > 0


def test_arm_b_converts_a_scripted_reply():
    brief, ref = plan_30x40_g1()
    spec = build_spec(brief)
    assert "Buildable envelope" in prompt(brief, spec) and "habitable_room (largest) area >= 9.5" in prompt(brief, spec)
    reply = {"stair": {"flight_width": 1.0, "riser": 0.1667, "tread": 0.25, "risers_per_flight": 9},
             "floors": [{"level": f.level,
                         "rooms": [{"id": r.id, "type": r.type, "name": r.name, "polygon": [list(p) for p in r.polygon]}
                                   for r in f.rooms],
                         "doors": [{"between": list(d.between), "at": list(d.at), "width": d.width, "main": d.main}
                                   for d in f.doors],
                         "windows": [{"room": w.room, "at": list(w.at), "width": w.width, "height": w.height,
                                      "kind": w.kind} for w in f.windows],
                         "columns": [list(c.at) for c in f.columns]} for f in ref.floors]}
    llm = ScriptedClient(["not json", json.dumps(reply)])
    layout, log = draw(llm, brief, spec, "b")
    assert layout is not None and len(log) == 2 and "invalid" in log[0]
    s, _ = score_layout(layout, spec)
    assert s["pass"], s["failures"]


def test_summary_counts_missing_plans_as_failures():
    lay = {"pass": True, "code": [10, 10], "passed": 20, "checks": 20, "skipped": 0, "n_failures": 0,
           "reachable": True, "programme": True}
    recs = [{"arm": "B", "id": "1", "produced": True, "layout": lay, "staad": True, "building_valid": True,
             "ifc_errors": None, "seconds": 10, "llm_calls": 1, "status": "layout", "notes": []},
            {"arm": "B", "id": "2", "produced": False, "layout": None, "staad": None, "building_valid": None,
             "ifc_errors": None, "seconds": 5, "llm_calls": 3, "status": "stopped:layout",
             "advice": ["one more storey (fits)"], "notes": []}]
    t = summarise(recs)["B"]
    assert t["plan_produced"] == 0.5 and t["rule_check_pass"] == 0.5 and t["staad_pass"] == 0.5
    assert t["code_checks_passed"] == 1.0 and t["stopped_with_advice"] == 0.5 and t["refused_out_of_scope"] == 0
    assert "| Design delivered | 50% |" in table_markdown(summarise(recs))
