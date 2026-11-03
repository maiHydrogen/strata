"""Tests for the client brief schema and its matrix defaults."""

import json
import os

import pytest
from pydantic import ValidationError

from src.brief.schema import MUST, Brief, Plot, Road, Site

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "examples", "g1_house.brief.json")


def test_empty_brief_misses_every_must_item():
    assert {path for path, _ in Brief().missing_must()} == set(MUST)


def test_defaults_fill_gaps_and_say_so():
    brief = Brief(floors_now=2, site=Site(plot=Plot(width=9.1, depth=15.2)))
    full = brief.with_defaults()
    assert full.sources["floors_now"] == "client" and full.sources["site.plot"] == "client"
    assert full.sources["site.roads"] == "default (B4)" and full.site.roads == [Road(side="front", width=6.0)]
    assert full.floors_future == 3 and full.sources["floors_future"] == "default (A2)"
    assert full.programme.bedrooms == 4 and full.programme.bathrooms_attached == 2  # 2BHK per floor
    assert full.site.city == "Guwahati" and full.site.north is None  # B3 has no safe default
    assert brief.sources == {}  # the input brief is untouched
    # a defaulted Must item is still a question for the chatbot
    assert ("site.roads", "B4") in full.missing_must() and ("floors_now", "A2") not in full.missing_must()


def test_plot_has_no_default():
    assert Brief().with_defaults().site.plot is None


def test_invalid_briefs_are_rejected():
    with pytest.raises(ValidationError):
        Brief(floors_now=3, floors_future=2)
    with pytest.raises(ValidationError):
        Plot(width=-1, depth=10)
    with pytest.raises(ValidationError):
        Brief(floors_now=4)  # G+3 is outside the G+1/G+2 typology


def test_example_brief_and_json_schema():
    with open(EXAMPLE) as f:
        brief = Brief.model_validate_json(f.read())
    assert brief.missing_must() == [] and brief.site.main_road.width == 6.0
    schema = Brief.model_json_schema()  # the chatbot's output contract
    assert {"programme", "site", "floors_now"} <= set(schema["properties"])
    json.dumps(schema)
