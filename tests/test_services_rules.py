"""The services constants (src/services/templates.py) match the NBC values in the knowledge graph."""

import pytest

from src.inference.spec import build_spec
from src.services import templates as T
from src.spatial.sample_layout import sample_brief


@pytest.fixture(scope="module")
def spec():
    return build_spec(sample_brief())


def value(spec, element, quantity, comparator=None):
    for e in spec["services"]:
        if (e["element"], e["quantity"]) == (element, quantity) and comparator in (None, e["comparator"]):
            return e
    raise AssertionError(f"{element}.{quantity} missing from the spec")


def test_services_constants_match_the_graph(spec):
    assert value(spec, "light_circuit", "points")["value"] == T.LIGHT_CIRCUIT_POINTS
    assert value(spec, "light_circuit", "load")["value"] == T.LIGHT_CIRCUIT_W
    assert value(spec, "building", "lighting_density")["value"] == T.LIGHTING_W_PER_M2_MAX
    assert value(spec, "building", "power_density")["value"] == T.POWER_W_PER_M2_MIN
    assert value(spec, "overhead_tank", "storage_fraction", "le")["value"] == T.OHT_FRACTION
    beds = sample_brief().programme.bedrooms
    assert value(spec, "dwelling_unit", "occupants")["value"] == T.POPULATION_BY_BEDROOMS.get(beds, T.POPULATION_4_PLUS)
    assert value(spec, "socket_outlet", "height")["value"] <= min(T.FIXTURE[k][1] for k in ("socket_6a", "socket_16a"))


def test_services_sources_cite_the_same_clauses_as_the_graph(spec):
    for key, (el, q) in {"subcircuit": ("light_circuit", "points"), "lighting_density": ("building", "lighting_density"),
                         "overhead_tank": ("overhead_tank", "storage_fraction"),
                         "population": ("dwelling_unit", "occupants"), "socket_height": ("socket_outlet", "height")}.items():
        src = value(spec, el, q)["source"]
        assert (src["clause"], src["page"]) == (T.SOURCES[key]["clause"], T.SOURCES[key]["page"]), key


def test_septic_tank_rules_are_in_the_spec(spec):
    assert value(spec, "septic_tank", "width")["value"] == 0.75
    assert value(spec, "septic_tank", "length_width_ratio", "le")["value"] == 4.0
