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


# ---- plot and road read by code when the LLM leaves them out

from src.brief.chat import BriefSession, Extraction as _X, _site_from_text  # noqa: E402
from src.brief.llm import ScriptedClient  # noqa: E402


@pytest.mark.parametrize("text, width, depth, unit", [
    ("a 60 x 80 square feet plot inside it have 6 feet space from all side", 60, 80, "ft"),
    ("size of plot is 60x80 ft and width of road is 20 feet with 60 feet side facing road", 60, 80, "ft"),
    ("plot 40 by 60 feet, the 60 feet side faces the road", 60, 40, "ft"),
    ("12 x 18 m plot", 12, 18, "m"),
])
def test_plot_sides_from_plain_words(text, width, depth, unit):
    x = _site_from_text(text, _X(unsure=["site.plot"]))
    assert (x.plot_width.value, x.plot_depth.value, x.plot_width.unit) == (width, depth, unit)
    assert "site.plot" not in x.unsure


def test_road_width_from_plain_words_and_no_false_plot():
    x = _site_from_text("3 bedrooms, 2 x 2 bathrooms, road of 20 feet", _X())
    assert x.plot_width is None and x.roads[0].width.value == 20 and x.roads[0].side == "front"


def test_llm_values_are_never_overridden():
    from src.brief.chat import Length
    given = _X(plot_width=Length(value=30, unit="ft"), plot_depth=Length(value=50, unit="ft"))
    assert _site_from_text("60 x 80 feet", given).plot_width.value == 30


def test_plot_marked_unsure_by_the_llm_is_still_taken_from_the_message():
    llm = ScriptedClient([{"unsure": ["site.plot"]}])
    s = BriefSession(llm)
    s.start()
    s.asked = "site.plot"
    s.reply("60 x 80 square feet plot with 60 feet side facing road of 20 feet")
    assert round(s.brief.site.plot.width, 2) == 18.29 and round(s.brief.site.plot.depth, 2) == 24.38
    assert round(s.brief.site.roads[0].width, 2) == 6.10


@pytest.mark.parametrize("text, floors", [("it's a groundfloor only building", 1), ("only ground floor", 1),
                                          ("ground floor bedroom for my parents", None), ("G+1, ground floor only kitchen", 2)])
def test_ground_floor_only_means_one_storey(text, floors):
    from src.brief.chat import _floors_from_text
    assert _floors_from_text(text, _X()).floors_now == floors


def test_a_road_wider_than_a_street_is_not_taken_and_the_client_is_told():
    llm = ScriptedClient([{"roads": [{"side": "front", "width": {"value": 200, "unit": "ft"}}]}])
    s = BriefSession(llm)
    s.start()
    reply = s.reply("the road is wide")  # no width in the words, so only the LLM's value
    assert s.brief.site.roads == [] and "wider than a residential street" in reply


def test_a_plainly_stated_road_width_wins_over_a_misread():
    llm = ScriptedClient([{"roads": [{"side": "front", "width": {"value": 200, "unit": "ft"}}]}])
    s = BriefSession(llm)
    s.start()
    s.reply("road of 20 feet in front")
    assert [round(r.width, 2) for r in s.brief.site.roads] == [6.10]


def test_plan_form_wishes_from_plain_words():
    from src.brief.chat import _form_from_text
    text = ("inside the main premise there should be one main central open varandah surrounded by a kitchen 4 bedrooms "
            "distributed symmetrically around varandah and staircase for the terrace")
    assert _form_from_text(text) == {"courtyard": True, "roof_stair": True, "symmetric": True}
    assert _form_from_text("open space in front for parking, surrounding open spaces") == {}
    assert _form_from_text("G+1 with a terrace") == {}


def test_plan_form_wishes_reach_the_brief_and_its_summary():
    s = BriefSession(ScriptedClient([{}]))
    s.start()
    s.reply("we want an aangan in the middle and a staircase to the roof")
    assert s.brief.courtyard and s.brief.roof_stair and not s.brief.symmetric
    assert s.brief.sources["courtyard"] == "client"


@pytest.mark.parametrize("text, now, future", [
    ("it's a groundfloor only building", 1, 1), ("G+1 only", 2, 2), ("G+1 now, G+2 later", 2, 3),
    ("G+1, may add a floor later", 2, None)])
def test_only_fixes_the_future_floors(text, now, future):
    from src.brief.chat import _floors_from_text
    x = _floors_from_text(text, _X())
    assert (x.floors_now, x.floors_future) == (now, future)


def test_ground_floor_only_brief_is_designed_for_one_storey():
    s = BriefSession(ScriptedClient([{}, {}]))
    s.start()
    s.reply("a groundfloor only building")
    assert (s.brief.floors_now, s.brief.floors_future) == (1, 1)
    assert s.final_brief().floors_future == 1  # not the A2 default of one more
    s2 = BriefSession(ScriptedClient([{"floors_now": 2}, {}]))
    s2.start()
    s2.reply("two storeys")
    s2.reply("no more floors later")
    assert s2.brief.floors_future == 2


def test_ground_floor_for_now_keeps_the_future_open():
    from src.brief.chat import _floors_from_text
    x = _floors_from_text("Only the ground floor for now, but we want to add one more floor in the future", _X())
    assert x.floors_now == 1 and x.floors_future is None  # the LLM's 2 stands
