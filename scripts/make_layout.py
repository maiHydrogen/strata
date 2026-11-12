"""Brief -> spec -> GA layouts -> rule check -> layout JSON and SVG preview.

Run: python scripts/make_layout.py examples/g1_house.brief.json [--restarts 5] [--seed 1] [--top 3]
Writes output/layouts/<name>_<k>.layout.json, .svg and .check.json for the best distinct layouts.
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.brief.schema import Brief  # noqa: E402
from src.inference.spec import build_spec  # noqa: E402
from src.spatial.checker import check_layout, format_report  # noqa: E402
from src.spatial.ga import alternatives, evaluate, suggest, to_layout  # noqa: E402
from src.spatial.preview import render_svg  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("brief")
    ap.add_argument("--restarts", type=int, default=8)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--llm-seeds", action="store_true",
                    help="start the GA from Gemma's layout sketches too (off by default: in testing they did not help)")
    args = ap.parse_args()
    with open(args.brief) as f:
        brief = Brief.model_validate(json.load(f)).with_defaults()
    spec = build_spec(brief)
    seeds = []
    if args.llm_seeds:
        from dotenv import load_dotenv
        from src.brief.llm import GeminiClient
        from src.spatial.ga import problem
        from src.spatial.proposals import genome_from_proposal, propose
        load_dotenv(os.path.join(ROOT, ".env"))
        p0 = problem(brief, spec)
        examples = []
        try:
            from src.spatial.library import describe, load_index, load_layouts, retrieve
            prog = brief.programme
            rows = retrieve(load_index(), p0.envelope[2], p0.envelope[3], prog.bedrooms,
                            prog.bathrooms_attached + prog.bathrooms_common, k=3)
            examples = [describe(lay) for lay in load_layouts([r["id"] for r in rows])]
        except FileNotFoundError:
            pass
        sketches, _ = propose(GeminiClient(), p0, examples)
        seeds = [genome_from_proposal(s, p0) for s in sketches]
        print(f"{len(seeds)} LLM sketches added to the starting population")
    genomes, p = alternatives(brief, spec, count=args.top, restarts=args.restarts, seed=args.seed, seeds=seeds)
    name = os.path.basename(args.brief).split(".")[0]
    out = os.path.join(ROOT, "output", "layouts")
    os.makedirs(out, exist_ok=True)
    k = 0
    for g in genomes:
        hard, soft, why, plan = evaluate(g, p, detail=True)
        k += 1
        layout = to_layout(g, p, name=f"{name}_{k}")
        result = check_layout(layout, spec)
        base = os.path.join(out, f"{name}_{k}")
        with open(base + ".layout.json", "w") as f:
            f.write(layout.model_dump_json(indent=1))
        with open(base + ".check.json", "w") as f:
            json.dump(result, f, indent=1)
        with open(base + ".svg", "w") as f:
            f.write(render_svg(layout, result))
        print(f"[{k}] GA hard {hard:.1f} soft {soft:.3f}" + (f"; does not fit: {why}" if why else ""))
        print(format_report(result))
    print(f"Wrote {k} layouts to {out}")
    if genomes and evaluate(genomes[0], p, detail=True)[0] > 0:
        print("\nThe brief does not fit this plot as asked. Changes that would make it fit:")
        for r in suggest(brief, build_spec, seed=args.seed):
            print(f"  {'fits ' if r['fits'] else 'no   '} {r['change']}" + ("" if r["fits"] else f" ({r['problems'][0]})"))


if __name__ == "__main__":
    main()
