"""Phase 9, optional item: STRATA's GA layout against the hand-drawn reference plan for the same brief.

Run: python scripts/eval_reference.py [--restarts 8]
Writes output/eval/reference_compare.json and output/eval/reference_compare.md; GA layouts and their
SVGs go to output/eval/reference/ for a look side by side with output/reference_plans/.
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.evaluation.reference_compare import compare, sample_compare  # noqa: E402
from src.spatial.preview import render_svg  # noqa: E402

COLS = [("pass", "Passes every rule"), ("built_up", "Built-up (m2)"), ("carpet_ratio", "Carpet / built-up"),
        ("circulation_share", "Circulation share of carpet"), ("mean_habitable", "Mean habitable room (m2)")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--restarts", type=int, default=8)
    args = ap.parse_args()
    out = os.path.join(ROOT, "output", "eval")
    pics = os.path.join(out, "reference")
    os.makedirs(pics, exist_ok=True)
    rows = []
    for key in ("30x40_g1", "30x50_g1", "30x50_g2", "40x60_g1"):
        r = compare(key, restarts=args.restarts) if key != "40x60_g1" else sample_compare(restarts=args.restarts)
        ga = r.pop("strata_layout", None)
        if ga is not None:
            with open(os.path.join(pics, f"ga_{key}.layout.json"), "w") as f:
                f.write(ga.model_dump_json(indent=1))
            with open(os.path.join(pics, f"ga_{key}.svg"), "w") as f:
                f.write(render_svg(ga))
        rows.append(r)
        print(json.dumps(r), flush=True)
    with open(os.path.join(out, "reference_compare.json"), "w") as f:
        json.dump(rows, f, indent=1)
    lines = ["| Plot | Plan | " + " | ".join(c for _, c in COLS) + " | Rule failures |", "|---|---|" + "---|" * (len(COLS) + 1)]
    for r in rows:
        for who, label in (("reference", "hand-drawn"), ("strata", "STRATA GA")):
            m = r.get(who)
            if not m:
                lines.append(f"| {r['plan']} | {label} | " + " | ".join("-" for _ in COLS) + " | no layout |")
                continue
            cells = [("yes" if m[k] else "no") if k == "pass" else f"{m[k]:g}" if m[k] is not None else "-"
                     for k, _ in COLS]
            lines.append(f"| {r['plan']} | {label} | " + " | ".join(cells) + f" | {'; '.join(m['failures'][:2]) or '-'} |")
    md = "# STRATA vs hand-drawn reference plans\n\n" + "\n".join(lines) + "\n"
    with open(os.path.join(out, "reference_compare.md"), "w") as f:
        f.write(md)
    print(md)


if __name__ == "__main__":
    main()
