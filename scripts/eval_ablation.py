"""Phase 9 ablation: arms A (LLM writes the model), B (LLM + knowledge graph) and C (full STRATA) on the
25 gold briefs, scored by the same rule checker, STAAD loop and building-JSON validation.

Run: python scripts/eval_ablation.py [--arms A,B,C] [--only b01,b05] [--all] [--no-staad] [--refresh]
Arms A and B run on the 10 briefs of gold_briefs.LLM_SUBSET (--all: every brief), arm C on all 25.
Needs GEMINI_API_KEY (or, with --provider groq, GROQ_API_KEY) in .env for arms A and B (their LLM replies are cached per brief under
output/runs/ablation/<arm>/<brief>/, so a rerun makes no API calls unless --refresh); STAAD reachable for
the structure stage of B and C (--no-staad scores the plans only). Writes output/eval/ablation.json
(records + summary) and output/eval/ablation_table.md / ablation_cases.csv.
"""

import argparse
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

from src.evaluation.ablation import OUT, NoStaad, run_case, summarise, table_markdown  # noqa: E402
from src.evaluation.gold_briefs import LLM_SUBSET, gold_cases  # noqa: E402
from src.inference.spec import build_spec  # noqa: E402

EVAL = os.path.join(ROOT, "output", "eval")


def line(r):
    lay = r["layout"] or {}
    return (f"{r['arm']} {r['id']:28s} {str(r['status']):18s} plan {'Y' if r['produced'] else '-'} "
            f"rules {'PASS' if lay.get('pass') else 'fail' if lay else '-':4s} "
            f"code {lay.get('code', ['-', '-'])[0]}/{lay.get('code', ['-', '-'])[1]} "
            f"reach {'Y' if lay.get('reachable') else '-'} prog {'Y' if lay.get('programme') else '-'} "
            f"staad {r['staad']} bldg {r['building_valid']} | {r['seconds']} s, {r['llm_calls']} calls")


def write_csv(records, path):
    cols = ["arm", "id", "status", "produced", "rule_pass", "code_passed", "code_checks", "failures", "reachable",
            "programme", "staad", "building_valid", "services", "ifc_errors", "seconds", "llm_calls", "first_failures"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in records:
            lay = r["layout"] or {}
            w.writerow([r["arm"], r["id"], r["status"], r["produced"], lay.get("pass"), (lay.get("code") or [None])[0],
                        (lay.get("code") or [None, None])[1], lay.get("n_failures"), lay.get("reachable"),
                        lay.get("programme"), r["staad"], r["building_valid"],
                        "/".join(map(str, r["services"])) if r["services"] else "", r["ifc_errors"], r["seconds"],
                        r["llm_calls"], "; ".join(lay.get("failures", [])[:3])])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="A,B,C")
    ap.add_argument("--only", default="", help="comma-separated brief id prefixes")
    ap.add_argument("--all", action="store_true", help="arms A and B on every brief, not only the 10 of LLM_SUBSET")
    ap.add_argument("--no-staad", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="ask the LLM again instead of using cached replies")
    ap.add_argument("--provider", default="gemini", help="gemini (Gemma on the free tier) or groq (gpt-oss-120b)")
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    load_dotenv(os.path.join(ROOT, ".env"))
    arms = args.arms.split(",")
    cases = [(c, b) for c, b in gold_cases() if not args.only or any(c["id"].startswith(p) for p in args.only.split(","))]
    llm = None
    if {"A", "B"} & set(arms):
        from src.brief.llm import GeminiClient, GroqClient
        if args.provider == "groq":
            llm = GroqClient(model=args.model, retries=6)
        else:
            # the free tier answers 503 at busy times (wait it out); a whole model or plan takes minutes to write
            llm = GeminiClient(model=args.model, retries=4, timeout_ms=1_800_000)
        llm.on_retry = lambda m: print(f"    {m}", flush=True)
    if args.no_staad:
        runner = NoStaad()
    else:
        from src.structure.staad_runner import StaadRunner
        runner = StaadRunner()
    path = os.path.join(EVAL, "ablation.json")
    for c, brief in cases:
        spec = build_spec(brief)
        for arm in arms:
            if arm in "AB" and not args.all and c["id"] not in LLM_SUBSET:
                continue
            r = run_case(arm, c, brief, spec, llm=llm, runner=runner, refresh=args.refresh)
            print(line(r), flush=True)
            if r["status"] == "crash" and any("LLMError" in str(n) for n in r["notes"]):
                print("    (LLM service failure, not a result: not saved; rerun to retry this brief)", flush=True)
                continue
            save(path, r)  # after every case, so an interrupted run keeps what finished
    records = load()
    md = report(records)
    print("\n" + md)
    print(f"\nWrote {path}")


def load(path=None):
    """Every record so far, one record.json per arm and brief (arm C and the LLM arms may run as separate
    processes, so each writes only its own files)."""
    records = []
    for arm in sorted(os.listdir(OUT)) if os.path.isdir(OUT) else []:
        for cid in sorted(os.listdir(os.path.join(OUT, arm))) if os.path.isdir(os.path.join(OUT, arm)) else []:
            f = os.path.join(OUT, arm, cid, "record.json")
            if os.path.exists(f):
                with open(f) as fh:
                    records.append(json.load(fh))
    return records


def save(path, rec):
    folder = os.path.join(OUT, rec["arm"], rec["id"])
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "record.json"), "w") as f:
        json.dump(rec, f, indent=1, default=str)
    records = load()
    os.makedirs(EVAL, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"summary": {"subset": summarise(records, set(LLM_SUBSET)), "all": summarise(records)},
                   "records": records}, f, indent=1, default=str)
    os.replace(tmp, path)


def report(records):
    """The results tables: all arms on the 10 LLM-arm briefs, and every arm on every brief it ran."""
    sub = table_markdown(summarise(records, set(LLM_SUBSET)))
    full = table_markdown(summarise(records))
    n = len({r["id"] for r in records})
    md = (f"# Phase 9 ablation\n\n## Same {len(LLM_SUBSET)} briefs, all arms\n\n{sub}\n\n"
          f"## Every brief each arm ran ({n} briefs in all)\n\n{full}\n")
    with open(os.path.join(EVAL, "ablation_table.md"), "w") as f:
        f.write(md)
    write_csv(records, os.path.join(EVAL, "ablation_cases.csv"))
    return md


if __name__ == "__main__":
    main()
