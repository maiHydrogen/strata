"""Build the floor-plan library from ResPlan: convert, index and rule-check every plan.

Needs the ResPlan download (git-ignored):
    git clone --depth 1 https://github.com/m-agour/ResPlan.git data/plan_library/resplan/repo
    unzip data/plan_library/resplan/repo/ResPlan.zip -d data/plan_library/resplan/raw

Writes data/plan_library/library.jsonl (one layout JSON per plan), data/plan_library/index.json
(features for retrieval and floor pairing, with each plan's rule-check summary) and
data/plan_library/report.json (conversion and rule-check statistics, room adjacency).
Run: python scripts/build_plan_library.py [--limit N]
"""

import argparse
import json
import os
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.brief.schema import Brief  # noqa: E402
from src.inference.spec import build_spec  # noqa: E402
from src.spatial.checker import check_layout  # noqa: E402
from src.spatial.library import (INDEX, LIBRARY, RESPLAN_SPLIT, adjacency_stats, convert, features,  # noqa: E402
                                 load_resplan)

SKIP = {"programme", "setbacks", "planning", "building", "materials"}  # need a brief or plot; library plans have none


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    t0 = time.time()
    plans = load_resplan()
    if args.limit:
        plans = plans[:args.limit]
    split = {i: s for s, ids in json.load(open(RESPLAN_SPLIT)).items() for i in ids}
    spec = build_spec(Brief(city="Guwahati").with_defaults().model_copy(update={"site": Brief().site}))
    dropped, failed_checks, index = Counter(), Counter(), []
    clean = 0
    with open(LIBRARY, "w") as out:
        for n, plan in enumerate(plans, 1):
            layout, quality = convert(plan, split.get(plan["id"]))
            if layout is None:
                dropped[quality.split(":")[0].split(" (")[0].split(" overlap")[0]] += 1
                continue
            result = check_layout(layout, spec)
            fails = [r for r in result["results"] if r["pass"] is False and r["category"] not in SKIP]
            row = features(layout, quality)
            row["check"] = {"validity": sum(r["basis"] == "validity" for r in fails),
                            "code": sum(r["basis"] == "code" for r in fails),
                            "practice": sum(r["basis"] == "practice" for r in fails)}
            clean += row["check"]["validity"] == 0
            for r in fails:
                failed_checks[f"{r['basis']}: {r['check'].split(' (')[0][:70]}"] += 1
            index.append(row)
            out.write(layout.model_dump_json() + "\n")
            if n % 2000 == 0:
                print(f"{n} plans, {len(index)} kept, {time.time() - t0:.0f} s", flush=True)
    with open(INDEX, "w") as f:
        json.dump({"source": "ResPlan (CC BY 4.0), https://github.com/m-agour/ResPlan", "plans": index}, f)
    report = {"plans": len(plans), "converted": len(index), "dropped": dict(dropped),
              "no_validity_failures": clean, "with_stair": sum(1 for p in index if p["stair"]),
              "failed_checks": dict(failed_checks.most_common()), "door_adjacency": adjacency_stats(index),
              "seconds": round(time.time() - t0, 1)}
    with open(INDEX.parent / "report.json", "w") as f:
        json.dump(report, f, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k not in ("failed_checks", "door_adjacency")}, indent=2))
    print("top failures:", json.dumps(dict(list(failed_checks.most_common(12))), indent=1))


if __name__ == "__main__":
    main()
