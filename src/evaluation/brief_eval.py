"""Evaluate the brief chatbot on the synthetic test set (evaluation/brief_test_set.json).

Each case runs a full conversation: the client's opening message, then a simulated
client answering each follow-up question from the case's script, then 'yes' to the
summary. Scored against the hand-written gold values:
- extraction: per stated fact, correct / wrong / missed after the opening message;
  values the client never stated are hallucinations ('building_use' guessed from
  context is counted apart, as an inference);
- omission detection: the bot asks exactly for the Must items the message left out;
- completeness: after the follow-ups, every gold fact is in the final brief and the
  brief has no unanswered Must item except those the client could not answer.
"""

import json
import time
from pathlib import Path

from src.brief.chat import BriefSession

TEST_SET = Path(__file__).resolve().parents[2] / "evaluation" / "brief_test_set.json"
LENGTH_TOL = 0.02  # relative, for metres converted from feet
MUST_KEY = {"building_use": "building_use", "floors_now": "floors_now", "family": "persons", "programme": "bedrooms",
            "site.plot": "plot_width", "site.north": "north", "site.roads": "roads", "site.city": "city"}
INFERABLE = {"building_use"}
MAX_TURNS = 12


def load_cases(path=TEST_SET):
    with open(path) as f:
        return json.load(f)["cases"]


def flatten(brief):
    """Client-given values of a Brief, in the test set's flat vocabulary."""
    src, out = brief.sources, {}
    client = lambda path: src.get(path) == "client"  # noqa: E731
    for k in ("building_use", "floors_now", "floors_future", "vastu", "lift"):
        if client(k) and getattr(brief, k) is not None:
            out[k] = getattr(brief, k)
    if client("family"):
        f = brief.family
        out.update({"persons": f.persons, "elderly": f.elderly, "children": f.children, "disabled": f.disabled})
    if client("programme"):
        p = brief.programme
        out.update({k: getattr(p, k) for k in ("bedrooms", "bathrooms_attached", "bathrooms_common", "kitchen", "puja",
                                               "study", "store", "wc_type")})
    if client("parking"):
        out.update({"cars": brief.parking.cars, "two_wheelers": brief.parking.two_wheelers})
    s = brief.site
    if client("site.plot"):
        out.update({"plot_width": s.plot.width, "plot_depth": s.plot.depth})
    if client("site.north"):
        out["north"] = s.north
    if client("site.roads"):
        out["roads"] = [[r.side, r.width] for r in s.roads]
    if client("site.city"):
        out["city"] = s.city
    return out


# defaults the flattening reports for any given family/programme; not counted as hallucinations
IMPLIED = {"elderly": 0, "children": 0, "disabled": 0, "puja": False, "study": False, "store": False,
           "kitchen": "separate_dining", "wc_type": "western", "cars": 0, "two_wheelers": 0}


def same(key, gold, got):
    if key in ("plot_width", "plot_depth"):
        return got is not None and abs(got - gold) <= LENGTH_TOL * gold
    if key == "roads":
        return (got is not None and len(got) == len(gold)
                and all(gs == s and abs(w - gw) <= LENGTH_TOL * gw for (gs, gw), (s, w) in zip(sorted(gold), sorted(got))))
    if key == "city":
        return got is not None and gold.lower() in got.lower()
    return got == gold


def score_facts(gold, got):
    """(correct, wrong, missed, hallucinated, inferred) lists of keys."""
    correct, wrong, missed = [], [], []
    for k, v in gold.items():
        if k not in got:
            missed.append(k)
        elif same(k, v, got[k]):
            correct.append(k)
        else:
            wrong.append(k)
    extra = [k for k, v in got.items() if k not in gold and IMPLIED.get(k, object()) != v
             and not (k == "floors_future" and v == got.get("floors_now"))
             # bathroom counts default to one attached + one common per floor (A5) once bedrooms are given
             and not (k in ("bathrooms_attached", "bathrooms_common") and "bedrooms" in gold)]
    inferred = [k for k in extra if k in INFERABLE]
    return correct, wrong, missed, [k for k in extra if k not in INFERABLE], inferred


def run_case(case, llm, pause=0.0, log_dir=None):
    log = Path(log_dir) / f"{case['id']}.jsonl" if log_dir else None
    if log and log.exists():
        log.unlink()
    s = BriefSession(llm, log_path=log)
    s.start()
    t0, calls = time.time(), 1
    s.reply(case["message"])
    after_first = flatten(s.brief)
    asks_expected = {p for p, k in MUST_KEY.items() if k not in case["stated"]}
    asks_actual = {p for p, _ in s.brief.missing_must()} - s.accepted_defaults
    asked, turns, unanswered = [], 0, []
    while not s.summarised and turns < MAX_TURNS:
        path = s.asked
        asked.append(path)
        answer = case["answers"].get(path)
        if answer is None:
            unanswered.append(path)
        time.sleep(pause)
        s.reply(answer["text"] if answer else "I don't know.")
        turns += 1
        calls += 1
    if s.summarised:
        s.reply("yes")
    gold_final = dict(case["stated"])
    for a in case["answers"].values():
        gold_final.update(a["facts"])
    final_brief = s.final_brief() if s.done else s.brief
    final = flatten(final_brief)
    c1, w1, m1, h1, i1 = score_facts(case["stated"], after_first)
    cf, wf, mf, hf, _ = score_facts(gold_final, final)
    unsure = {p for p, a in case["answers"].items() if not a["facts"]}
    return {
        "id": case["id"], "done": s.done, "turns": turns, "llm_calls": calls, "seconds": round(time.time() - t0, 1),
        "first": {"correct": c1, "wrong": w1, "missed": m1, "hallucinated": h1, "inferred": i1,
                  "got": {k: after_first.get(k) for k in w1 + h1}},
        "omissions": {"expected": sorted(asks_expected), "actual": sorted(asks_actual),
                      "exact": asks_expected == asks_actual},
        "asked": asked, "unanswered": unanswered,
        "final": {"correct": cf, "wrong": wf, "missed": mf, "hallucinated": hf,
                  # only the items the client could not answer may remain unanswered
                  "complete": s.done and not wf and not mf
                              and all(p in unsure for p, _ in final_brief.missing_must())},
    }


def summarise(results, model):
    n = len(results)
    first = [r["first"] for r in results]
    stated = sum(len(f["correct"]) + len(f["wrong"]) + len(f["missed"]) for f in first)
    correct = sum(len(f["correct"]) for f in first)
    extracted = correct + sum(len(f["wrong"]) + len(f["hallucinated"]) for f in first)
    return {
        "model": model, "cases": n,
        "extraction_recall": round(correct / stated, 3) if stated else None,
        "extraction_precision": round(correct / extracted, 3) if extracted else None,
        "wrong_values": sum(len(f["wrong"]) for f in first),
        "hallucinated_values": sum(len(f["hallucinated"]) for f in first),
        "inferred_building_use": sum(len(f["inferred"]) for f in first),
        "omission_detection_exact": round(sum(r["omissions"]["exact"] for r in results) / n, 3),
        "conversations_completed": sum(r["done"] for r in results),
        "briefs_complete_and_correct": sum(r["final"]["complete"] for r in results),
        "mean_llm_calls": round(sum(r["llm_calls"] for r in results) / n, 2),
        "mean_seconds": round(sum(r["seconds"] for r in results) / n, 1),
    }
