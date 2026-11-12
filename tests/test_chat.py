"""Tests for the brief chatbot with a scripted LLM (no network, no API key)."""

import json

import pytest

from src.brief.chat import BriefSession, Extraction
from src.brief.llm import LLMError, ScriptedClient

FIRST = {"building_use": "single_family", "floors_now": 2, "floors_future": 3, "persons": 5,
         "members": [{"relation": "parents", "count": 2, "age": "elderly"}],
         "bedrooms": 3, "puja": True, "plot_width": {"value": 30, "unit": "ft"},
         "plot_depth": {"value": 50, "unit": "ft"}, "city": "Guwahati"}


def test_one_message_with_most_facts_then_questions_for_the_rest(tmp_path):
    llm = ScriptedClient([FIRST, {"north": "E"}, {"roads": [{"side": "front", "width": {"value": 20, "unit": "ft"}}]}])
    s = BriefSession(llm, log_path=tmp_path / "log.jsonl")
    s.start()
    q = s.reply("G+1 for 5 of us incl. my parents, 3BHK with puja room, plot 30x50 ft in Guwahati, may add a floor")
    assert s.asked == "site.north" and "front of your plot" in q
    assert s.brief.site.plot.width == 9.144 and s.brief.site.plot.depth == 15.24  # converted in code, not by the LLM
    assert s.brief.programme.bedrooms == 3 and s.brief.programme.bathrooms_attached == 2  # A5 until said
    s.reply("east facing")
    summary = s.reply("about 20 feet")
    assert s.summarised and "Is this correct?" in summary and "9.14 m x 15.24 m" in summary
    assert s.reply("yes") and s.done
    brief = s.final_brief()
    assert brief.site.roads[0].width == 6.096 and brief.sources["site.roads"] == "client"
    assert brief.missing_must() == []
    log = [json.loads(line) for line in open(tmp_path / "log.jsonl")]
    assert len(log) == 3 and log[0]["model"] == "scripted" and log[0]["parsed"]["bedrooms"] == 3


def test_invalid_json_is_retried_with_the_error():
    llm = ScriptedClient(['{"floors_now": "two"}', {"floors_now": 2}])
    s = BriefSession(llm)
    s.reply("two floors")
    assert s.brief.floors_now == 2 and len(llm.calls) == 2
    assert "invalid" in llm.calls[1]["prompt"] and llm.calls[1]["schema"] is Extraction


def test_gives_up_after_three_bad_answers_and_asks_again():
    s = BriefSession(ScriptedClient(["not json"] * 3))
    s.asked = "site.city"
    answer = s.reply("hmm")
    assert answer.startswith("Sorry") and s.asked == "building_use" and s.brief.sources == {}


def test_unsure_accepts_the_default_but_never_for_the_plot():
    s = BriefSession(ScriptedClient([FIRST, {"unsure": ["site.north"]}, {"unsure": ["site.roads"]}]))
    s.reply("...")
    s.reply("no idea which way it faces")
    summary = s.reply("don't know the road width")
    assert s.summarised and "Assumed" in summary and "which way the plot faces" in summary
    brief = s.final_brief()
    assert brief.sources["site.roads"] == "default (B4), client unsure" and brief.site.roads[0].width == 6.0
    s2 = BriefSession(ScriptedClient([{"unsure": ["site.plot"]}]))
    s2.asked = "site.plot"
    s2.reply("not sure")
    assert "site.plot" not in s2.accepted_defaults


def test_corrections_after_the_summary_update_the_brief():
    s = BriefSession(ScriptedClient([FIRST | {"north": "E", "roads": [{"side": "front", "width": {"value": 6,
                                                                                                    "unit": "m"}}]},
                                     {"bedrooms": 4}]))
    s.reply("...")
    assert s.summarised
    again = s.reply("actually make it 4 bedrooms")
    assert s.brief.programme.bedrooms == 4 and s.brief.programme.puja and "4 bedrooms" in again and not s.done


def test_family_members_are_added_up_in_code():
    s = BriefSession(ScriptedClient([{"members": [
        {"relation": "parents", "count": 2, "age": "elderly"}, {"relation": "wife"},
        {"relation": "kids", "count": 2, "age": "child"}, {"relation": "me"}]}]))
    s.reply("my parents, my wife, our two kids and me")
    f = s.brief.family
    assert (f.persons, f.elderly, f.children, f.disabled) == (6, 2, 2, 0)
    s2 = BriefSession(ScriptedClient([{"persons": 8, "members": [{"relation": "parents", "count": 2,
                                                                  "age": "elderly"}]}]))
    s2.reply("joint family of 8 including my parents in their seventies")
    assert (s2.brief.family.persons, s2.brief.family.elderly) == (8, 2)
    bad = BriefSession(ScriptedClient([{"members": [{"relation": "family", "count": -2}]}, {"persons": 4}]))
    bad.reply("...")
    assert bad.brief.family.persons == 4  # the negative count was rejected and asked again


def test_an_answer_cannot_overwrite_other_client_answers():
    s = BriefSession(ScriptedClient([{"building_use": "home_with_shop", "bedrooms": 2},
                                     {"building_use": "single_family", "persons": 3, "bedrooms": 5,
                                      "city": "Dispur, Guwahati"}]))
    s.reply("house with a shop below, 2BHK")
    s.asked = "family"
    s.reply("my wife, our son and me")
    b = s.brief
    assert b.building_use == "home_with_shop" and b.programme.bedrooms == 2  # kept
    assert b.family.persons == 3 and b.site.city == "Dispur, Guwahati" and b.site.in_ulb  # new facts fill in


def test_session_state_round_trip_resumes_the_conversation():
    s = BriefSession(ScriptedClient([FIRST]))
    s.reply("...")
    state = json.loads(json.dumps(s.to_state()))  # through JSON, as the web API does
    llm = ScriptedClient([{"north": "E"}])
    again = BriefSession.from_state(llm, state)
    assert again.asked == "site.north" and again.brief.programme.bedrooms == 3
    again.reply("east")
    assert again.brief.site.north == "E" and again.asked == "site.roads"


def test_llm_failure_does_not_crash_the_session():
    class Down:
        model = "down"

        def complete_json(self, *a):
            raise LLMError("429 quota")
    s = BriefSession(Down())
    assert s.reply("hello").startswith("Sorry")


def test_rate_limiter_paces_requests_and_tokens():
    from src.brief.llm import RateLimiter
    now = [0.0]
    lim = RateLimiter(rpm=2, tpm=1000, rpd=5, clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))
    for tokens in (300, 300):
        lim.wait(300)
        lim.record(tokens)
    assert now[0] == 0.0
    lim.wait(300)  # third request in the same minute waits for the first to age out
    assert now[0] >= 60
    lim.record(900)
    lim.wait(300)  # 900 + 300 tokens exceeds 1000 in the minute
    assert now[0] >= 120
    lim.record(10)  # fourth request
    lim.wait(1)
    lim.record(10)  # fifth: the daily limit of 5 is used up
    with pytest.raises(LLMError, match="daily"):
        lim.wait(1)


def test_json_wrapped_in_a_code_fence_is_still_read():
    s = BriefSession(ScriptedClient([' {"floors_now": 2, "city": "Guwahati"}\n```', '```json\n{"bedrooms": 3}\n```']))
    s.reply("G+1 in Guwahati")
    assert s.brief.floors_now == 2 and s.brief.site.city == "Guwahati"
    bad = BriefSession(ScriptedClient(["no json here", '{"floors_now": 2}']))
    bad.reply("two floors")
    assert bad.brief.floors_now == 2  # the first reply was rejected and asked again


def test_schema_sent_to_the_model_has_no_numeric_bounds():
    import json as _json
    from src.brief.llm import model_schema
    sent = _json.dumps(model_schema(Extraction))
    assert "minimum" not in sent and "Minimum" not in sent
    assert "minimum" in _json.dumps(Extraction.model_json_schema())  # pydantic still enforces them


def test_live_gemini_extraction():
    """Runs only with GEMINI_API_KEY set (in the environment or .env)."""
    import os

    from dotenv import load_dotenv
    load_dotenv()
    if not os.environ.get("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY not set")
    from src.brief.llm import GeminiClient
    s = BriefSession(GeminiClient())
    s.reply("We want a G+1 house, 3BHK, on a 30 by 40 feet east facing plot in Guwahati")
    assert s.brief.floors_now == 2 and s.brief.programme.bedrooms == 3
    assert abs(s.brief.site.plot.width - 9.144) < 0.01 and s.brief.site.north == "E"
