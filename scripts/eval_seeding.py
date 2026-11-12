"""Does seeding the layout GA with LLM proposals help? Compares, on one brief:
  - the LLM proposals on their own (decoded and scored, no search),
  - the GA from random genomes,
  - the GA with the proposals in its starting population,
over several random seeds, recording the best score at fixed generations.

Run: python scripts/eval_seeding.py examples/g1_40x60.brief.json [--runs 10] [--refresh]
Proposals are cached in output/layouts/proposals_<name>.json (reused unless --refresh), so a rerun
makes no API calls. Results: output/eval/seeding_<name>.json.
"""

import argparse
import json
import os
import statistics as st
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

from src.brief.schema import Brief  # noqa: E402
from src.inference.spec import build_spec  # noqa: E402
from src.spatial.ga import _run, evaluate, problem  # noqa: E402
from src.spatial.proposals import Sketch, genome_from_proposal, propose  # noqa: E402

CHECKPOINTS = (0, 5, 10, 20, 40)


def proposals(name, p, brief, refresh):
    path = os.path.join(ROOT, "output", "layouts", f"proposals_{name}.json")
    if os.path.exists(path) and not refresh:
        with open(path) as f:
            cached = json.load(f)
        if cached.get("sketches"):
            return [Sketch(**s) for s in cached["sketches"]], cached.get("examples", [])
    from src.brief.llm import GeminiClient
    examples = []
    try:
        from src.spatial.library import describe, load_index, load_layouts, retrieve
        prog = brief.programme
        rows = retrieve(load_index(), p.envelope[2], p.envelope[3], prog.bedrooms,
                        prog.bathrooms_attached + prog.bathrooms_common, k=3)
        examples = [describe(lay) for lay in load_layouts([r["id"] for r in rows])]
    except FileNotFoundError:
        pass  # no plan library here: propose without examples
    sketches, log = propose(GeminiClient(), p, examples, count=4)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"examples": examples, "sketches": [s.model_dump() for s in sketches], "log": log}, f, indent=1)
    return sketches, examples


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("brief")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--pop", type=int, default=40)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    load_dotenv(os.path.join(ROOT, ".env"))
    with open(args.brief) as f:
        brief = Brief.model_validate(json.load(f)).with_defaults()
    spec = build_spec(brief)
    p = problem(brief, spec)
    name = os.path.basename(args.brief).split(".")[0]
    sketches, examples = proposals(name, p, brief, args.refresh)
    seeds = [genome_from_proposal(s, p) for s in sketches]

    raw = []
    for s, g in zip(sketches, seeds):
        hard, soft, why, _ = evaluate(g, p, detail=True)
        raw.append({"focus": s.idea, "feasible": hard == 0, "score": round(hard + soft, 3), "problems": why[:3]})

    arms = {"random start": [], "seeded with LLM proposals": []}
    for run in range(args.runs):
        for arm, start in (("random start", ()), ("seeded with LLM proposals", seeds)):
            _, _, history = _run(brief, spec, args.pop, max(CHECKPOINTS), 100 + run, start)
            arms[arm].append(history)

    def at(histories, gen):
        values = [h[max(gen - 1, 0)] if gen else h[0] for h in histories]
        return round(st.mean(values), 3), sum(v < 1000 for v in values)

    table = {arm: {f"gen {g}": {"mean best": at(hs, g)[0], "feasible runs": at(hs, g)[1]} for g in CHECKPOINTS}
             for arm, hs in arms.items()}
    out = {"brief": name, "runs": args.runs, "population": args.pop, "examples_from_library": len(examples),
           "llm_proposals": raw, "ga": table}
    os.makedirs(os.path.join(ROOT, "output", "eval"), exist_ok=True)
    with open(os.path.join(ROOT, "output", "eval", f"seeding_{name}.json"), "w") as f:
        json.dump(out, f, indent=1)
    print(f"LLM proposals alone ({len(raw)}, {len(examples)} library examples in the prompt):")
    for r in raw:
        print(f"  {'feasible' if r['feasible'] else 'infeasible':10s} score {r['score']:9.2f}  {r['focus']}")
    print(f"\nGA, mean best score over {args.runs} runs (population {args.pop}); lower is better, feasible runs in brackets")
    print("  " + " " * 28 + "".join(f"{f'gen {g}':>16s}" for g in CHECKPOINTS))
    for arm, row in table.items():
        print(f"  {arm:28s}" + "".join(f"{row[f'gen {g}']['mean best']:>11.2f} ({row[f'gen {g}']['feasible runs']:>2d})"
                                       for g in CHECKPOINTS))


if __name__ == "__main__":
    main()
