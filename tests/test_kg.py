"""Tests for the standards knowledge graph (src/inference/kg.py, data/knowledge_graph)."""

import pytest
import rdflib

from src.inference.kg import KnowledgeGraph
from src.structure.std_writer import Loads, SeismicParams


@pytest.fixture(scope="module")
def kg():
    return KnowledgeGraph()


def test_graph_conforms_to_shapes(kg):
    assert kg.validate() == []


def test_requirement_without_citation_is_rejected():
    kg = KnowledgeGraph()
    kg.add_turtle("""
        @prefix strata: <https://strata.iitg.ac.in/kg#> . @prefix kg: <https://strata.iitg.ac.in/kg/id/> .
        kg:req_uncited a strata:Requirement ; strata:appliesTo strata:HabitableRoom ;
            strata:quantity strata:area ; strata:comparator "ge" ; strata:value 9.0 ; strata:unit "m2" ;
            strata:jurisdiction strata:India ; strata:layer "A" ; strata:status "transcribed" .
    """)
    problems = kg.validate()
    assert problems and all("req_uncited" in p for p in problems)


def test_habitable_room_size_with_citation(kg):
    v = kg.lookup("area", "habitable_room", room_role="principal")
    assert (v.value, v.unit, v.comparator) == (9.5, "m2", "ge")
    assert v.clause == "Vol 1, Part 3, cl. 12.2.2" and v.page == "44"
    assert "9.5 m2" in v.quote and v.status == "transcribed"
    assert kg.lookup("area", "habitable_room", room_role="other").value == 7.5
    assert kg.lookup("width", "habitable_room", room_role="other").value == 2.1
    assert kg.lookup("height", "habitable_room").value == 2.75


def test_missing_condition_names_the_context_needed(kg):
    with pytest.raises(LookupError, match="room_role"):
        kg.lookup("area", "habitable_room")
    with pytest.raises(LookupError, match="occupancy"):
        kg.lookup("riser", "staircase")


def test_kitchen_and_sanitary_sizes(kg):
    assert kg.lookup("area", "kitchen", kitchen_type="separate_dining").value == 5.0
    assert kg.lookup("area", "kitchen", kitchen_type="with_store").value == 4.5
    assert kg.lookup("width", "kitchen", kitchen_type="with_dining").value == 2.1
    assert (kg.lookup("area", "bathroom").value, kg.lookup("area", "wc").value,
            kg.lookup("area", "bath_wc").value) == (1.8, 1.1, 2.8)


def test_staircase_for_one_or_two_family_dwelling(kg):
    stair = {q: kg.lookup(q, "staircase", occupancy="A-2") for q in ("width", "tread", "riser", "headroom")}
    assert {q: v.value for q, v in stair.items()} == {"width": 1.0, "tread": 0.25, "riser": 0.19, "headroom": 2.2}
    assert stair["riser"].comparator == "le" and stair["riser"].satisfied_by(0.175)
    assert not stair["riser"].satisfied_by(0.2)
    assert kg.lookup("risers_per_flight", "staircase").value == 12


def test_parapet_has_both_limits(kg):
    with pytest.raises(ValueError, match="comparator"):
        kg.lookup("height", "parapet")
    assert kg.lookup("height", "parapet", comparator="ge").value == 1.0
    assert kg.lookup("height", "parapet", comparator="le").value == 1.2


def test_ventilation_uses_guwahati_climate_and_records_the_assumption(kg):
    v = kg.lookup("opening_ratio", "room")
    assert v.value == 0.1667 and v.clause == "Vol 1, Part 3, cl. 20.1.2"
    assert len(v.assumptions) == 1 and "warm-humid" in v.assumptions[0] and "assumed" in v.assumptions[0]
    assert kg.lookup("opening_ratio", "room", climate="cold").value == 0.0834
    assert kg.lookup("opening_ratio", "room", climate="cold").assumptions == ()


def test_guwahati_site_facts(kg):
    facts = kg.site_facts("guwahati")
    assert facts["seismic_zone"].text == "V" and facts["zone_factor"].value == 0.36
    assert facts["wind_speed"].value == 50.0 and facts["climate"].status == "assumed"
    assert "Annex K" in facts["zone_factor"].clause
    assert kg.site_facts("india") == {}


def test_staad_defaults_match_the_graph(kg):
    """The structural stage's built-in loads and seismic zone are the cited code values."""
    loads, seismic = Loads(), SeismicParams()
    assert loads.floor_live == kg.lookup("imposed_load", "floor", room_use="rooms").value
    assert loads.roof_live == kg.lookup("imposed_load", "roof", roof_access="access").value
    assert seismic.zone_factor == kg.site_facts("guwahati")["zone_factor"].value


def test_water_demand_by_population(kg):
    assert kg.lookup("water_demand", "building", population=1_000_000).value == 150.0
    assert kg.lookup("water_demand", "building", population=50_000).value == 100.0
    with pytest.raises(LookupError):
        kg.lookup("water_demand", "building", population=5_000)


def test_local_requirement_overrides_national_and_weaker_ones_are_flagged():
    kg = KnowledgeGraph()
    kg.add_turtle("""
        @prefix strata: <https://strata.iitg.ac.in/kg#> . @prefix kg: <https://strata.iitg.ac.in/kg/id/> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        kg:TestByelaw a strata:Standard ; rdfs:label "Test byelaw" ; strata:edition "1" ;
            strata:sourceFile "none" ; strata:legallyBinding true .
        kg:test_clause a strata:Clause ; strata:inStandard kg:TestByelaw ; strata:clauseId "cl. 1" ;
            strata:page "1" ; strata:quote "A test clause for the precedence rule." .
        kg:req_local a strata:Requirement ; strata:appliesTo strata:HabitableRoom ;
            strata:quantity strata:height ; strata:comparator "ge" ; strata:value 2.6 ; strata:unit "m" ;
            strata:citedBy kg:test_clause ; strata:jurisdiction strata:Assam ; strata:layer "A" ;
            strata:status "transcribed" .
    """)
    assert kg.validate() == []
    v = kg.lookup("height", "habitable_room")
    assert (v.value, v.jurisdiction, v.standard) == (2.6, "assam", "Test byelaw")
    assert kg.lookup("height", "habitable_room", jurisdiction="india").value == 2.75
    flagged = [r for r in kg.query("local_overrides") if r["local"].endswith("req_local")]
    assert len(flagged) == 1 and flagged[0]["weaker"] is True and flagged[0]["widerJur"] == "india"


def test_jurisdiction_chain_most_specific_first(kg):
    assert [r["jurKey"] for r in kg.query("jurisdictions", siteKey="guwahati")] == ["guwahati", "assam", "india"]


def test_audit_queries(kg):
    unverified = kg.query("unverified")
    assert unverified[0]["status"] == "assumed"  # assumed values listed first
    strata = "https://strata.iitg.ac.in/kg#"
    requirements = set(kg.graph.subjects(rdflib.RDF.type, rdflib.URIRef(strata + "Requirement")))
    assert len(unverified) == len(requirements)  # nobody has signed off a value yet
    coverage = {r["item"]: r for r in kg.query("matrix_coverage")}
    assert {"D1", "D2", "D3", "D4", "D6", "D7", "D8", "D9", "D12"} <= set(coverage)
    assert coverage["D4"]["assumed"] == 1
    structural = kg.query("by_layer", layer="C")
    assert any(r["quantityKey"] == "imposed_load" for r in structural)
    cited = kg.query("citations")
    assert all(r["requirements"] >= 0 for r in cited) and any(r["clauseId"].endswith("12.2.2") for r in cited)


def test_guwahati_setbacks_by_road_width_and_height(kg):
    """Bye-law 24(a): a G+1/G+2 house (height up to 9.6 m) on a 6 m road."""
    front = kg.lookup("front_setback", "plot", road_width=6.0, building_height=9.0)
    assert (front.value, front.jurisdiction) == (3.6, "guwahati")
    assert front.clause.startswith("Bye-law 24(a)(i)") and front.standard.startswith("Assam Unified")
    assert kg.lookup("side_setback", "plot", building_height=9.0).value == 1.8
    assert kg.lookup("rear_setback", "plot", building_height=11.0).value == 2.4
    # band edges: 'up to 6.6' includes 6.6, 'more than 6.6' starts above it
    assert kg.lookup("front_setback", "plot", road_width=6.6, building_height=9.6).value == 3.6
    assert kg.lookup("front_setback", "plot", road_width=6.7, building_height=9.6).value == 4.5
    assert kg.lookup("front_setback", "plot", road_width=6.6, building_height=9.7).value == 4.5


def test_guwahati_plot_size_and_far(kg):
    assert kg.lookup("area", "plot", in_ulb="yes").value == 134.0
    assert kg.lookup("width", "plot", in_ulb="no").value == 7.5
    base = kg.lookup("far", "plot", road_width=6.0)
    assert (base.value, base.comparator, base.status) == (1.25, "le", "assumed")  # 2025 amendment ambiguity
    assert kg.lookup("far_max", "plot", road_width=10.0, plot_area=900.0).value == 1.75
    with pytest.raises(LookupError, match="plot_area"):
        kg.lookup("far_max", "plot", road_width=10.0)


def test_byelaws_do_not_apply_outside_guwahati(kg):
    with pytest.raises(LookupError):
        kg.lookup("front_setback", "plot", jurisdiction="india", road_width=6.0, building_height=9.0)
    standards = {r["standard"] for r in kg.query("citations")}
    assert any(s.startswith("Assam Unified") for s in standards) and any(s.startswith("National Building Code") for s in standards)


def test_guwahati_parking_for_a_house(kg):
    """Appendix-I: car spaces by the dwelling unit's carpet area; space sizes include circulation."""
    assert kg.lookup("parking", "dwelling_unit", carpet_area=90.0).value == 1.0
    assert kg.lookup("parking", "dwelling_unit", carpet_area=120.0).value == 2.0
    assert kg.lookup("two_wheeler_parking", "dwelling_unit", carpet_area=60.0).value == 1.0
    with pytest.raises(LookupError):
        kg.lookup("parking", "dwelling_unit", carpet_area=60.0)  # small units need a two-wheeler space only
    assert kg.lookup("area", "car_space", parking_type="open").value == 20.0
    assert kg.lookup("width", "driveway").value == 3.6


def test_guwahati_rainwater_harvesting(kg):
    pits = kg.lookup("percolation_pits", "plot", building_type="house")
    assert pits.value == 0.01 and "Appendix-VI" in pits.clause and pits.page == "9809"
    assert kg.lookup("rainwater_storage", "plot", building_type="house", plot_area=200.0).text == "required"
    with pytest.raises(LookupError):
        kg.lookup("rainwater_storage", "plot", building_type="house", plot_area=120.0)
