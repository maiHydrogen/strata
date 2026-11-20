"""Phase 9 ablation: the same briefs through three systems, scored by the same independent checks.

  A  LLM writes the BIM model as code (the Text2BIM-style baseline on `strata_demo`)   src/evaluation/baseline.py
  B  LLM draws the layout from the brief and the knowledge graph, no search            src/evaluation/llm_layout.py
  C  full STRATA: knowledge graph, GA, rule checker, STAAD loop, services             src/orchestrator.py

Every arm's plan goes through the same rule checker (src/spatial/checker.py), which no arm sees while
designing. B and C also go through the same STAAD design loop and building-JSON validation; A makes no
structure, so its STAAD result is "not produced". One record per (arm, brief); `summarise` reduces
them to the results table.
"""

import json
import os
import time
import traceback

from src.execution.building_schema import Building, validate_building
from src.execution.layout_to_building import building_from_layout
from src.services.generate import add_services, prefs_from_brief, tank_from_brief
from src.spatial.checker import check_layout
from src.spatial.layout import Layout

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "output", "runs", "ablation")
ARMS = {"A": "LLM writes the model (Text2BIM-style)", "B": "LLM + knowledge graph", "C": "full STRATA"}
CODE_NOTE = "Phase 9 ablation"


class NoStaad:
    """Stands in for STAAD when it is not reachable: the structure stage then reports 'not run'."""

    def __call__(self, std_text, name):
        raise RuntimeError("STAAD not run (--no-staad)")


# ---- scoring -------------------------------------------------------------------------------------

def score_layout(layout, spec):
    """Rule-checker metrics for one plan."""
    check = check_layout(layout, spec)
    items = check["results"]
    done = [r for r in items if r["pass"] is not None]

    def rate(basis):
        xs = [r for r in done if r["basis"] == basis]
        return [sum(r["pass"] for r in xs), len(xs)]

    reach = [r for r in items if r["check"].startswith("every room reachable")]
    prog = [r for r in items if r["category"] == "programme"]
    fails = [f"{r['subject']}: {r['check']}" for r in done if not r["pass"] and r["basis"] in ("code", "validity")]
    return {
        "pass": check["pass"],  # no code or validity failure
        "checks": len(done), "passed": sum(r["pass"] for r in done), "skipped": len(items) - len(done),
        "code": rate("code"), "validity": rate("validity"), "practice": rate("practice"),
        "reachable": bool(reach) and all(r["pass"] for r in reach),
        "programme": bool(prog) and all(r["pass"] for r in prog),
        "floors": len(layout.floors), "rooms": sum(len(f.rooms) for f in layout.floors),
        "built_up": check["areas"].get("built_up_total"),
        "failures": fails[:12], "n_failures": len(fails),
    }, check


def downstream(layout, brief, spec, runner, name, out):
    """STAAD design loop, then the building JSON with services, for a plan made outside the orchestrator."""
    from src.structure.pipeline import design_structure

    rec = {"staad": None, "staad_note": None, "building_valid": None, "services": None}
    try:
        result = design_structure(layout, spec, runner, name, brief=brief, max_rounds=10)
    except Exception as e:  # noqa: BLE001 - a frame that cannot be built or run is the finding
        rec["staad"] = False
        rec["staad_note"] = f"{type(e).__name__}: {str(e)[:200]}"
        if "STAAD not run" in str(e):
            rec["staad"] = None
        return rec
    rec["staad"] = bool(result.passed)
    rec["staad_rounds"] = len(result.outcome.rounds)
    if not result.passed:
        rec["staad_note"] = result.outcome.reason
        return rec
    try:
        b = building_from_layout(layout, result.model, result.outcome, result.footings, CODE_NOTE, name=name)
        add_services(b, tank_from_brief(brief, spec), prefs_from_brief(brief))
        problems = validate_building(Building.model_validate(b))
        rec["building_valid"] = not problems
        rec["building_problems"] = problems[:5]
        sv = b["services"]["checks"]
        rec["services"] = [sum(c["pass"] for c in sv), len(sv)]
        with open(os.path.join(out, f"{name}.building.json"), "w") as f:
            json.dump(b, f)
    except Exception as e:  # noqa: BLE001
        rec["building_valid"] = False
        rec["building_problems"] = [f"{type(e).__name__}: {str(e)[:200]}"]
    return rec


def _record(arm, case, **kw):
    r = {"arm": arm, "id": case["id"], "produced": False, "status": None, "layout": None, "staad": None,
         "building_valid": None, "services": None, "ifc_errors": None, "llm_calls": 0, "seconds": None, "notes": []}
    r.update(kw)
    return r


# ---- arms ----------------------------------------------------------------------------------------

def run_a(case, brief, spec, llm, refresh=False):
    from src.evaluation.baseline import run_arm_a
    from src.evaluation.gold_briefs import brief_text
    from src.evaluation.ifc_layout import ifc_to_layout

    out = os.path.join(OUT, "A", case["id"])
    r = run_arm_a(brief_text(case), llm, out, refresh=refresh, log=lambda m: print(m, flush=True))
    rec = _record("A", case, llm_calls=r["llm_calls"], seconds=r["seconds"], status="ifc" if r["ok"] else "no ifc",
                  llm_model=llm.model)
    if not r["ok"]:
        rec["notes"].append(r["log"][-600:])
        return rec
    val = r.get("validation") or {}
    rec["ifc_errors"] = len(val.get("errors", []))
    rec["ifc_counts"] = val.get("counts")
    layout, notes = ifc_to_layout(r["ifc"], f"A_{case['id']}",
                                  {"width": round(brief.site.plot.width, 4), "depth": round(brief.site.plot.depth, 4)})
    rec["notes"] += notes[:10]
    if layout is None:
        rec["status"] = "ifc without rooms"
        return rec
    with open(os.path.join(out, "layout.json"), "w") as f:
        f.write(layout.model_dump_json(indent=1))
    rec["produced"] = True
    rec["layout"], check = score_layout(layout, spec)
    with open(os.path.join(out, "check.json"), "w") as f:
        json.dump(check, f, indent=1)
    rec["staad"] = "not produced"
    return rec


def run_b(case, brief, spec, llm, runner, refresh=False):
    from src.evaluation.llm_layout import BLayout, draw, to_layout
    from src.spatial.proposals import first_json_object

    out = os.path.join(OUT, "B", case["id"])
    os.makedirs(out, exist_ok=True)
    from src.evaluation.baseline import cache_name

    cache = os.path.join(out, cache_name("llm", llm))
    t0 = time.time()
    name = f"B_{case['id']}"
    layout, log, calls = None, [], 0
    if os.path.exists(cache) and not refresh:
        with open(cache) as f:
            log = json.load(f)
        ok = [e for e in log if "response" in e and "invalid" not in e]
        if ok:
            try:
                layout = to_layout(BLayout.model_validate(first_json_object(ok[-1]["response"])), name, brief)
            except Exception as e:  # noqa: BLE001
                log.append({"invalid": f"{type(e).__name__}: {e}"})
        llm_seconds = sum(e.get("seconds", 0) for e in log)
    else:
        print("    arm B: asking the LLM for the layout", flush=True)
        layout, log = draw(llm, brief, spec, name)
        calls = sum(1 for e in log if "response" in e)
        llm_seconds = round(time.time() - t0, 1)
        if not calls:  # the LLM service failed before any reply: not a result, and nothing to cache
            return _record("B", case, status="crash", notes=[f"LLMError: {e.get('error')}" for e in log][:1])
        with open(cache, "w") as f:
            json.dump(log, f, indent=1)
    rec = _record("B", case, llm_calls=sum(1 for e in log if "response" in e), new_llm_calls=calls,
                  llm_model=llm.model)
    if layout is None:
        rec["status"] = "no valid layout JSON"
        rec["notes"] += [e.get("invalid") or e.get("error") for e in log if e.get("invalid") or e.get("error")][:3]
        rec["seconds"] = round(llm_seconds, 1)
        return rec
    with open(os.path.join(out, "layout.json"), "w") as f:
        f.write(layout.model_dump_json(indent=1))
    rec["produced"] = True
    rec["status"] = "layout"
    rec["layout"], check = score_layout(layout, spec)
    with open(os.path.join(out, "check.json"), "w") as f:
        json.dump(check, f, indent=1)
    t1 = time.time()
    rec.update(downstream(layout, brief, spec, runner, name, out))
    rec["seconds"] = round(llm_seconds + time.time() - t1, 1)
    return rec


def run_c(case, brief, spec, runner, restarts=8, seed=1):
    from src.orchestrator import run_pipeline

    out = os.path.join(OUT, "C", case["id"])
    t0 = time.time()
    error = None
    try:
        path, building = run_pipeline(brief, f"C_{case['id']}", runner, out=out, restarts=restarts, seed=seed,
                                      log=lambda *a: None, copy_to=None, resume=True)
    except Exception as e:  # noqa: BLE001 - STAAD unreachable or a crash: score what finished
        error = f"{type(e).__name__}: {str(e)[:200]}"
        path = os.path.join(out, "run.json")
    with open(path) as f:
        m = json.load(f)
    st = {s["stage"]: s for s in m["stages"]}
    rec = _record("C", case, status=m["status"] + (f":{m.get('stopped_at')}" if m.get("stopped_at") else ""),
                  seconds=m.get("seconds", round(time.time() - t0, 1)), llm_calls=0)
    if error:
        rec["notes"].append(error)
    if m.get("reason"):
        rec["notes"].append(m["reason"])
    rec["advice"] = m.get("advice", [])[:3]
    # the plan scored: the approved one (after any re-layout for spans), else the GA's best option
    key = "layout_final" if "layout_final" in m["files"] else next(
        (k for k in ("layout_1",) if k in m["files"]), None)
    if "approval" in st and "layout_final" not in m["files"]:
        key = f"layout_{st['approval']['option']}"
    if key:
        with open(os.path.join(ROOT, m["files"][key])) as f:
            layout = Layout.model_validate_json(f.read())
        rec["produced"] = m["status"] == "complete" or "approval" in st
        rec["layout"], _ = score_layout(layout, spec)
    if "structure" in st:
        rec["staad"] = any(a["passed"] for a in st["structure"].get("attempts", []))
    elif m["status"] == "failed" and "STAAD not run" in (m.get("reason") or ""):
        rec["staad"] = None
    elif m.get("stopped_at") == "structure":
        rec["staad"] = False
    if "building" in st:
        rec["building_valid"] = True
        ok, n = st["building"]["services_checks"].split("/")
        rec["services"] = [int(ok), int(n)]
    elif m.get("stopped_at") == "building":
        rec["building_valid"] = False
    return rec


def run_case(arm, case, brief, spec, llm=None, runner=None, refresh=False):
    try:
        if arm == "A":
            return run_a(case, brief, spec, llm, refresh)
        if arm == "B":
            return run_b(case, brief, spec, llm, runner, refresh)
        return run_c(case, brief, spec, runner)
    except Exception as e:  # noqa: BLE001 - a crash of the arm is recorded, not raised
        return _record(arm, case, status="crash", notes=[f"{type(e).__name__}: {e}",
                                                        *traceback.format_exc().splitlines()[-4:]])


# ---- summary -------------------------------------------------------------------------------------

def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def summarise(records, ids=None):
    """Per arm: the metrics of the results table, over the briefs in `ids` (all if None). Rates are over
    all those briefs, so a plan the arm never produced counts as a failure of every later metric."""
    table = {}
    for arm in ARMS:
        rs = [r for r in records if r["arm"] == arm and (ids is None or r["id"] in ids)]
        if not rs:
            continue
        n = len(rs)
        lay = [r["layout"] for r in rs if r["layout"]]
        frac = lambda xs: round(sum(1 for x in xs if x) / n, 3)  # noqa: E731
        code = [x["code"] for x in lay]
        models = sorted({r.get("llm_model") for r in rs if r.get("llm_model")})
        table[arm] = {
            "system": ARMS[arm] + (f" [{', '.join(models)}]" if models else ""), "briefs": n,
            "plan_produced": frac(r["produced"] for r in rs),
            # STRATA declines instead of delivering a plan that breaks a rule: say so, apart from failures
            "stopped_with_advice": frac(str(r["status"]).startswith("stopped") and r.get("advice") for r in rs),
            "refused_out_of_scope": frac(any("NoEnvelope" in str(n) for n in r["notes"]) for r in rs),
            "rule_check_pass": frac(r["layout"] and r["layout"]["pass"] for r in rs),
            "code_checks_passed": round(sum(c[0] for c in code) / max(1, sum(c[1] for c in code)), 3),
            "checks_passed_of_all": _mean([x["passed"] / max(1, x["checks"] + x["skipped"]) for x in lay]),
            "mean_failures": _mean([x["n_failures"] for x in lay]),
            "every_room_reachable": frac(r["layout"] and r["layout"]["reachable"] for r in rs),
            "programme_met": frac(r["layout"] and r["layout"]["programme"] for r in rs),
            "staad_pass": None if arm == "A" else frac(r["staad"] is True for r in rs),
            "staad_run": None if arm == "A" else sum(r["staad"] is not None for r in rs),
            "building_json_valid": None if arm == "A" else frac(r["building_valid"] for r in rs),
            "ifc_written": frac(r["ifc_errors"] is not None for r in rs) if arm == "A" else None,
            "ifc_without_errors": frac(r["ifc_errors"] == 0 for r in rs) if arm == "A" else None,
            "mean_seconds": _mean([r["seconds"] for r in rs]),
            "mean_llm_calls": _mean([r["llm_calls"] for r in rs]),
        }
    return table


ROWS = [("plan_produced", "Design delivered"), ("stopped_with_advice", "Stopped with brief changes that would fit"),
        ("refused_out_of_scope", "Refused: city has no bylaw layer"), ("rule_check_pass", "Passes every code and validity rule"),
        ("code_checks_passed", "Code checks passed (all plans)"), ("mean_failures", "Mean rule failures per plan"),
        ("every_room_reachable", "Every room reachable"), ("programme_met", "Rooms asked for are present"),
        ("staad_pass", "STAAD design passes (IS 456/13920)"), ("building_json_valid", "Building JSON valid"),
        ("ifc_written", "IFC written"), ("ifc_without_errors", "IFC with no schema/geometry errors"),
        ("mean_seconds", "Mean time per design (s)"), ("mean_llm_calls", "Mean LLM calls per design")]


def table_markdown(table):
    arms = [a for a in ARMS if a in table]
    head = "| Metric | " + " | ".join(f"{a}: {table[a]['system']}" for a in arms) + " |"
    lines = [head, "|---|" + "---|" * len(arms)]
    pct = {"plan_produced", "stopped_with_advice", "refused_out_of_scope", "rule_check_pass", "code_checks_passed", "every_room_reachable", "programme_met",
           "staad_pass", "building_json_valid", "ifc_written", "ifc_without_errors"}
    for key, label in ROWS:
        cells = []
        for a in arms:
            v = table[a].get(key)
            cells.append("n/a" if v is None else f"{100 * v:.0f}%" if key in pct else f"{v:g}")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)
