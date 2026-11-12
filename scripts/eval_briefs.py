"""Run the brief chatbot on the synthetic test set and write a report.

Run: python scripts/eval_briefs.py [--pause SECONDS] [--only b01,b05]
Needs GEMINI_API_KEY in .env. Writes output/eval/briefs_<model>.json (summary + per case)
and the prompt logs output/eval/logs/<case>.jsonl.
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

from src.brief.llm import GeminiClient  # noqa: E402
from src.evaluation.brief_eval import load_cases, run_case, summarise  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pause", type=float, default=2.0, help="seconds between LLM calls (free-tier rate limits)")
    parser.add_argument("--only", default="", help="comma-separated case id prefixes")
    args = parser.parse_args()
    load_dotenv(os.path.join(ROOT, ".env"))
    llm = GeminiClient()
    cases = [c for c in load_cases() if not args.only or any(c["id"].startswith(p) for p in args.only.split(","))]
    out_dir = os.path.join(ROOT, "output", "eval")
    results = []
    for case in cases:
        r = run_case(case, llm, pause=args.pause, log_dir=os.path.join(out_dir, "logs"))
        results.append(r)
        f = r["first"]
        print(f"{r['id']:28s} first {len(f['correct'])}/{len(f['correct']) + len(f['wrong']) + len(f['missed'])}"
              f" wrong {f['wrong'] or '-'} missed {f['missed'] or '-'} halluc {f['hallucinated'] or '-'}"
              f" | asks {'ok' if r['omissions']['exact'] else 'DIFF'} | complete {r['final']['complete']}"
              f" | {r['llm_calls']} calls {r['seconds']} s", flush=True)
    summary = summarise(results, llm.model)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"briefs_{llm.model}.json")
    with open(path, "w") as fh:
        json.dump({"summary": summary, "results": results}, fh, indent=2)
    print(json.dumps(summary, indent=2))
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
