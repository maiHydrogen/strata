"""Tests for the brief -> spec inference (src/inference/spec.py)."""

import json

import pytest

from src.brief.schema import Brief, Plot, Programme, Road, Site
from src.inference.kg import KnowledgeGraph
from src.inference.spec import build_spec
from src.structure.std_writer import Loads


@pytest.fixture(scope="module")
def kg():
    return KnowledgeGraph()


def _brief(**site):
    return Brief(building_use="single_family", floors_now=2, floors_future=3,
                 programme=Programme(bedrooms=3, bathrooms_attached=2, bathrooms_common=1, store=True),
                 site=Site(plot=Plot(width=9.1, depth=15.2), city="Guwahati", in_ulb=True,
                           **({"roads": [Road(side="front", width=6.0)]} | site)))


def _find(entries, quantity, element=None):
    return [e for e in entries if e["quantity"] == quantity and (element is None or e["element"] == element)]


def test_every_value_carries_its_citation(kg):
    spec = build_spec(_brief(), kg)
    values = [r for room in spec["rooms"] for r in room["requirements"]]
    values += spec["building"] + spec["plot"] + spec["structure"] + spec["site"]
    assert len(values) > 60
    for v in values:
        assert v["source"]["clause"] and v["source"]["page"] and v["source"]["status"] in (
            "transcribed", "verified", "assumed")
    json.dumps(spec)


def test_rooms_get_nbc_minimums(kg):
    rooms = {r["name"]: r for r in build_spec(_brief(), kg)["rooms"]}
    assert _find(rooms["Living room"]["requirements"], "area")[0]["value"] == 9.5
    assert _find(rooms["Bedroom 3"]["requirements"], "area")[0]["value"] == 7.5
    kitchen = rooms["Kitchen"]["requirements"]
    assert _find(kitchen, "area")[0]["value"] == 5.0 and _find(kitchen, "opening_ratio")[0]["value"] == 0.2084
    assert _find(rooms["Common bathroom 1"]["requirements"], "area")[0]["value"] == 2.8
    assert [r["quantity"] for r in rooms["Store"]["requirements"]] == ["area", "height"]


def test_setbacks_use_the_height_the_structure_is_designed_for(kg):
    spec = build_spec(_brief(), kg)
    assert spec["context"]["building_height"]["value"] == 9.45  # 3 storeys x 3.0 m + 0.45 m plinth
    assert _find(spec["plot"], "front_setback")[0]["value"] == 3.6
    envelope = next(c for c in spec["checks"] if c["check"].startswith("buildable"))
    assert envelope["actual"]["width"] == 5.5 and envelope["actual"]["depth"] == 9.8


def test_corner_plot_keeps_a_front_setback_on_each_road(kg):
    spec = build_spec(_brief(roads=[Road(side="front", width=6.0), Road(side="left", width=9.0)]), kg)
    assert sorted(e["value"] for e in _find(spec["plot"], "front_setback")) == [3.6, 4.5]
    envelope = next(c for c in spec["checks"] if c["check"].startswith("buildable"))["actual"]
    assert envelope["setbacks"]["left"] == 4.5 and envelope["width"] == round(9.1 - 4.5 - 1.8, 3)
    assert spec["context"]["road_width"]["value"] == 9.0  # the wider road governs FAR
    assert _find(spec["plot"], "far")[0]["value"] == 1.5


def test_omitted_parameter_is_filled_with_default_and_citation(kg):
    """Phase 3 acceptance test: drop the road from the brief and check what the engine does."""
    brief = _brief()
    brief.site.roads = []
    spec = build_spec(brief, kg)
    assert spec["brief_sources"]["site.roads"] == "default (B4)"
    assert spec["context"]["road_width"] == {"value": 6.0, "from": "widest abutting road, default (B4)"}
    front = _find(spec["plot"], "front_setback")[0]
    assert front["value"] == 3.6 and front["source"]["clause"] == "Bye-law 24(a)(i), front setback table"
    assert front["inputs"] == {"road": "front", "road_width": 6.0, "building_height": 9.45}


def test_small_plot_fails_the_minimum_plot_check(kg):
    brief = _brief()
    brief.site.plot = Plot(width=5.5, depth=20.0)
    checks = {c["check"]: c for c in build_spec(brief, kg)["checks"]}
    assert checks["plot width >= 6.0 m"]["pass"] is False and checks["plot area >= 134.0 m2"]["pass"] is False


def test_rainwater_parking_and_gaps_are_reported(kg):
    spec = build_spec(_brief(), kg)
    assert _find(spec["plot"], "percolation_pits")[0]["required_count"] == 2  # 138.3 m2 plot
    assert _find(spec["plot"], "rainwater_storage")[0]["value"] == "required"
    assert spec["deferred"][0]["quantity"] == "parking" and spec["deferred"][0]["needs"] == ["carpet_area"]
    assert [u["quantity"] for u in spec["unresolved"]] == ["water_demand"]


def test_city_without_byelaws_falls_back_to_nbc(kg):
    brief = _brief()
    brief.site.city = "Shillong"
    spec = build_spec(brief, kg)
    assert spec["jurisdiction"] == "india" and spec["notes"]
    assert not _find(spec["plot"], "front_setback") and spec["site"] == []
    assert _find(spec["rooms"][0]["requirements"], "area")[0]["value"] == 9.5


def test_structure_values_match_staad_stage(kg):
    structure = build_spec(_brief(), kg)["structure"]
    rooms = next(e for e in structure if e["element"] == "floor" and e["value"] == 2.0)
    roof = next(e for e in structure if e["element"] == "roof")
    assert (rooms["value"], roof["value"]) == (Loads().floor_live, Loads().roof_live)
