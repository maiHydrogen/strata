"""Phase 9, optional item: STRATA's layouts against hand-drawn reference plans for the same briefs.

There are no CPWD type designs for private plots among the reference documents, so the comparison
uses the reference plans drawn for STRATA (src/spatial/reference_plans.py, plus the 40x60 ft sample
in src/spatial/sample_layout.py): what a designer would draw for a typical narrow Guwahati plot.
For each brief the GA's best option and the reference plan go through the same rule checker, and
their plan efficiency is compared: built-up and carpet area, the share of the carpet area taken by
circulation (corridors, foyers and stairs) and the mean habitable room size.
"""

from src.inference.spec import build_spec
from src.spatial.checker import check_layout
from src.spatial.ga import alternatives, evaluate, to_layout
from src.spatial.reference_plans import PLANS

CIRCULATION = {"corridor", "foyer", "stair"}
HABITABLE = {"living", "bedroom", "dining", "study"}


def metrics(layout, spec):
    check = check_layout(layout, spec)
    areas = check["areas"]
    carpet = {(f.level, r.id): areas["floors"][i]["rooms"].get(r.id, 0.0)
              for i, f in enumerate(layout.floors) for r in f.rooms}
    types = {(f.level, r.id): r.type for f in layout.floors for r in f.rooms}
    total = sum(carpet.values())
    circ = sum(a for k, a in carpet.items() if types[k] in CIRCULATION)
    hab = [a for k, a in carpet.items() if types[k] in HABITABLE]
    fails = [f"{r['subject']}: {r['check']}" for r in check["results"]
             if r["pass"] is False and r["basis"] in ("code", "validity")]
    return {"pass": check["pass"], "failures": fails[:6], "checks": check["summary"]["checks"],
            "built_up": areas.get("built_up_total"), "carpet": round(total, 2),
            "carpet_ratio": round(total / areas["built_up_total"], 3) if areas.get("built_up_total") else None,
            "circulation_share": round(circ / total, 3) if total else None,
            "mean_habitable": round(sum(hab) / len(hab), 2) if hab else None,
            "rooms": len(carpet), "far": areas.get("far")}


def compare(key, restarts=8, seed=1):
    brief, ref = PLANS[key]()
    spec = build_spec(brief)
    genomes, p = alternatives(brief, spec, count=1, restarts=restarts, seed=seed)
    out = {"plan": key, "reference": metrics(ref, spec)}
    if genomes:
        hard = evaluate(genomes[0], p, detail=True)[0]
        ga = to_layout(genomes[0], p, name=f"ga_{key}")
        out["strata"] = metrics(ga, spec) | {"ga_hard": round(hard, 2)}
        out["strata_layout"] = ga
    return out


def sample_compare(restarts=8, seed=1):
    """The 40x60 ft sample plan against STRATA on the sample's own brief."""
    from src.spatial.sample_layout import sample_brief, sample_layout

    brief = sample_brief().with_defaults()
    spec = build_spec(brief)
    genomes, p = alternatives(brief, spec, count=1, restarts=restarts, seed=seed)
    out = {"plan": "40x60_g1", "reference": metrics(sample_layout(), spec)}
    if genomes:
        ga = to_layout(genomes[0], p, name="ga_40x60_g1")
        out["strata"] = metrics(ga, spec) | {"ga_hard": round(evaluate(genomes[0], p, detail=True)[0], 2)}
        out["strata_layout"] = ga
    return out
