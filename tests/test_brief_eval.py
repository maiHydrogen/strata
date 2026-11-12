"""The brief evaluation itself: an oracle LLM that returns the gold facts must score perfectly."""

import json
import re

import pytest

from src.brief.chat import BriefSession
from src.brief.schema import MUST
from src.evaluation.brief_eval import MUST_KEY, load_cases, run_case, same, summarise

CASES = load_cases()


def _extraction(facts, asked):
    x = {}
    for k, v in facts.items():
        if k in ("plot_width", "plot_depth"):
            x[k] = {"value": v, "unit": "m"}
        elif k == "roads":
            x[k] = [{"side": side, "width": {"value": w, "unit": "m"}} for side, w in v]
        elif k in ("elderly", "children", "disabled"):  # the chatbot takes named people as members
            if v:
                age = {"elderly": "elderly", "children": "child"}.get(k, "adult")
                x.setdefault("members", []).append({"relation": k, "count": v, "age": age,
                                                    "wheelchair": k == "disabled"})
        else:
            x[k] = v
    if not facts and asked:
        x["unsure"] = [asked]
    return x


class Oracle:
    """Answers each prompt with the gold facts of the message it contains."""
    model = "oracle"

    def __init__(self):
        self.texts = {}
        for c in CASES:
            self.texts[c["message"]] = c["stated"]
            for a in c["answers"].values():
                self.texts[a["text"]] = a["facts"]

    def complete_json(self, system, prompt, schema):
        message = prompt.split("Client's message:\n", 1)[1].split("\n\nYour previous JSON", 1)[0]
        asked = re.search(r"about '([^']+)'", prompt)
        return json.dumps(_extraction(self.texts.get(message, {}), asked.group(1) if asked else None))


def test_test_set_is_consistent():
    assert len(CASES) == 25 and len({c["id"] for c in CASES}) == 25
    assert set(MUST_KEY) == set(MUST)
    for c in CASES:
        omitted = {p for p, k in MUST_KEY.items() if k not in c["stated"]}
        # every Must item left out of the message has a scripted answer, and only those
        assert omitted == set(c["answers"]), c["id"]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_oracle_scores_perfectly(case):
    r = run_case(case, Oracle())
    assert r["done"] and r["omissions"]["exact"] and not r["unanswered"], r
    assert not r["first"]["wrong"] and not r["first"]["missed"] and not r["first"]["hallucinated"], r["first"]
    assert r["final"]["complete"], r["final"]
    assert r["llm_calls"] == 1 + len(case["answers"])


def test_summary_metrics():
    results = [run_case(c, Oracle()) for c in CASES[:5]]
    s = summarise(results, "oracle")
    assert s["extraction_recall"] == 1.0 and s["extraction_precision"] == 1.0 and s["omission_detection_exact"] == 1.0
    assert s["briefs_complete_and_correct"] == 5


def test_tolerances():
    assert same("plot_width", 9.144, 9.1) and not same("plot_width", 9.144, 9.6)
    assert same("roads", [["front", 6.096], ["left", 4.572]], [["left", 4.57], ["front", 6.1]])
    assert same("city", "Guwahati", "Dispur, Guwahati") and not same("north", "E", "W")
