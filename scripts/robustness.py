"""Robustness run: the full pipeline (spec -> layout -> STAAD -> services -> building JSON) on varied briefs.

Run: python scripts/robustness.py [--only c01,c05] [--restarts 6]
Each case is a variation of the 40x60 example brief (plot, storeys, programme, roads, family, soil, use).
A case passes when the pipeline either completes with a valid building JSON and every services check
passing, or stops cleanly at the stage the case expects (an impossible brief must stop at the layout
with suggestions, not crash). Writes output/eval/robustness.json; run folders go to output/runs/robust_*.
STAAD must be reachable (it runs on the Windows side).
"""

import argparse
import copy
import json
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.brief.schema import Brief  # noqa: E402
from src.orchestrator import run_pipeline  # noqa: E402
from src.structure.staad_runner import StaadRunner  # noqa: E402

FT = 0.3048
with open(os.path.join(ROOT, "examples", "g1_40x60.brief.json")) as f:
    BASE = json.load(f)


def case(cid, note, expect="complete", **changes):
    b = copy.deepcopy(BASE)
    for path, value in changes.items():
        obj, *rest = path.split("__")
        if rest:
            b[obj][rest[0]] = value
        else:
            b[path] = value
    b["sources"] = {k: "client" for k in ("building_use", "floors_now", "floors_future", "family", "programme",
                                          "site.plot", "site.north", "site.roads", "site.city")}
    return {"id": cid, "note": note, "expect": expect, "brief": b}


def plot(w_ft, d_ft):
    return {"width": round(w_ft * FT, 3), "depth": round(d_ft * FT, 3)}


def prog(bed, att, com, **kw):
    return {"bedrooms": bed, "bathrooms_attached": att, "bathrooms_common": com, "living": 1, "dining": True,
            "kitchen": "separate_dining", "puja": False, "study": False, "store": False, "wc_type": "western", **kw}


CASES = [
    case("c01", "baseline: G+1, 40x60 ft, 3 bed"),
    case("c02", "G+2, 30x50 ft, 4 bed", floors_now=3, floors_future=3, site__plot=plot(30, 50),
         programme=prog(4, 2, 1)),
    case("c03", "small G+1, 30x40 ft, 2 bed", site__plot=plot(30, 40), programme=prog(2, 1, 1)),
    case("c04", "narrow G+1, 25x50 ft, 2 bed", site__plot=plot(25, 50), programme=prog(2, 1, 1)),
    case("c05", "corner plot, roads front and left", site__plot=plot(30, 50), programme=prog(3, 1, 1),
         site__roads=[{"side": "front", "width": 6.0}, {"side": "left", "width": 4.5}]),
    case("c06", "north facing, economy finish", site__north="N", finish_level="economy"),
    case("c07", "large G+1, 50x80 ft, 4 bed + puja + study, premium", site__plot=plot(50, 80),
         programme=prog(4, 3, 1, puja=True, study=True, store=True), finish_level="premium"),
    case("c08", "G+2 joint family with elderly and a wheelchair user, 40x60 ft, 5 bed",
         floors_now=3, floors_future=3, programme=prog(5, 3, 2, puja=True),
         family={"persons": 8, "elderly": 2, "children": 2, "disabled": 1}),
    case("c09", "weak soil 60 kN/m2", site__soil_bearing_capacity=60.0),
    case("c10", "G+1 designed for a future floor (structure for 3 storeys)", floors_future=3),
    case("c11", "tight: G+2, 30x40 ft, 6 bed", floors_now=3, floors_future=3, site__plot=plot(30, 40),
         programme=prog(6, 3, 2)),
    case("c12", "impossible: G+1 on 20x30 ft with 4 bed", expect="stopped:layout", site__plot=plot(20, 30),
         programme=prog(4, 2, 1)),
    case("c13", "home with a shop, 30x50 ft", building_use="home_with_shop", site__plot=plot(30, 50),
         programme=prog(2, 1, 1)),
    case("c14", "defaults only: plot and city given, everything else assumed", programme=None, family=None,
         floors_now=None, floors_future=None, building_use=None),
]


def run_case(c, runner, restarts, seed):
    brief = Brief.model_validate(c["brief"]).with_defaults()
    t = time.time()
    log = []
    try:
        path, building = run_pipeline(brief, f"robust_{c['id']}", runner, restarts=restarts, seed=seed,
                                      log=log.append, copy_to=None)
    except Exception as e:  # noqa: BLE001 - a crash is the finding
        return {"id": c["id"], "note": c["note"], "status": "crash", "ok": False, "seconds": round(time.time() - t),
                "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc().splitlines()[-6:], "log": log[-8:]}
    m = json.load(open(path))
    out = {"id": c["id"], "note": c["note"], "status": m["status"], "seconds": round(time.time() - t),
           "stopped_at": m.get("stopped_at"), "reason": m.get("reason"), "advice": m.get("advice", [])[:3]}
    st = {s["stage"]: s for s in m["stages"]}
    if "layout" in st:
        out["layout_options"] = [(o["k"], o["pass"]) for o in st["layout"].get("options", [])]
    if "structure" in st:
        s = st["structure"]
        out["structure"] = {"attempts": s.get("attempts"), "sections": s.get("sections"), "slab_mm": s.get("slab_mm"),
                            "footings": s.get("footings")}
    if "building" in st:
        b = st["building"]
        out["counts"] = b["counts"]
        out["services_checks"] = b["services_checks"]
        out["services_failed"] = b["services_failed"]
    if c["expect"] == "complete":
        out["ok"] = m["status"] == "complete" and not out.get("services_failed")
    else:
        out["ok"] = f"{m['status']}:{m.get('stopped_at')}" == c["expect"] and bool(out["advice"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--restarts", type=int, default=6)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    cases = [c for c in CASES if not args.only or c["id"] in args.only.split(",")]
    runner = StaadRunner()
    results = []
    for c in cases:
        print(f"{c['id']} {c['note']} ...", flush=True)
        r = run_case(c, runner, args.restarts, args.seed)
        results.append(r)
        extra = r.get("services_checks") or r.get("reason") or r.get("error", "")
        print(f"  {'OK ' if r['ok'] else 'BAD'} {r['status']}{':' + r['stopped_at'] if r.get('stopped_at') else ''} "
              f"in {r['seconds']} s; {extra}", flush=True)
    os.makedirs(os.path.join(ROOT, "output", "eval"), exist_ok=True)
    path = os.path.join(ROOT, "output", "eval", "robustness.json")
    with open(path, "w") as f:
        json.dump({"cases": len(results), "ok": sum(r["ok"] for r in results), "results": results}, f, indent=1,
                  default=str)
    print(f"\n{sum(r['ok'] for r in results)}/{len(results)} cases OK; {path}")


if __name__ == "__main__":
    main()
