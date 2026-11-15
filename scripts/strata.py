"""STRATA in one command: brief -> spec -> layouts -> approval -> STAAD design -> services -> building JSON.

Run: python scripts/strata.py examples/g1_40x60.brief.json        (a confirmed brief, e.g. from the web app)
     python scripts/strata.py --chat my_house                       (chat in the terminal first; needs GEMINI_API_KEY)
Options:
     --approve      show the layout options and ask which one to build (default: the best passing one,
                    recorded in run.json as approved automatically)
     --pick K       build option K without asking
     --manual       write each STAAD .std and wait for you to run it (default: run SProStaad.exe hidden)
     --resume       carry on from output/runs/<name>/run.json: finished stages are loaded, not redone
                    (a changed brief or a different --pick redoes that stage and the ones after it)
     --name NAME    run name (default: the brief file's name)
     --top N --restarts N --seed N --attempts N --rounds N   layout GA and design loop settings

Writes output/runs/<name>/: the brief, spec, layout options (JSON, rule check, SVG), the approved
layout's .std and design.json, <layout>.building.json (also copied to the Windows STRATA folder) and
run.json (stages, timings, decisions, checks). Exit code 0 when the building JSON is written.
The Revit build and IFC export then run on Windows; run.json lists the steps.
"""

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.brief.schema import Brief  # noqa: E402
from src.orchestrator import run_pipeline  # noqa: E402
from src.structure.staad_runner import DEFAULT_WORKDIR, ManualRunner, StaadRunner  # noqa: E402


def chat(name):
    from dotenv import load_dotenv
    from src.brief.chat import BriefSession
    from src.brief.llm import GeminiClient
    load_dotenv(os.path.join(ROOT, ".env"))
    session = BriefSession(GeminiClient(), log_path=os.path.join(ROOT, "output", "logs", f"{name}.jsonl"))
    print(f"STRATA: {session.start()}")
    while not session.done:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit("\nStopped; nothing written.")
        if text:
            print(f"STRATA: {session.reply(text)}")
    return session.final_brief()


def ask(options):
    print("\nLayout options that pass the rule check:")
    for o in options:
        print(f"  [{o['k']}] {o['layout'].name}: GA soft penalty {o['soft']:.3f} (lower is better), preview "
              f"{o['layout'].name}.svg in the run folder")
    while True:
        try:
            k = int(input("Build which option? ").strip())
        except (ValueError, EOFError):
            continue
        hit = next((o for o in options if o["k"] == k), None)
        if hit:
            return hit


def main():
    ap = argparse.ArgumentParser(description="STRATA: brief to building JSON")
    ap.add_argument("brief", nargs="?")
    ap.add_argument("--chat", metavar="NAME")
    ap.add_argument("--name")
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--pick", type=int)
    ap.add_argument("--manual", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--restarts", type=int, default=8)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--rounds", type=int, default=10)
    args = ap.parse_args()
    if bool(args.brief) == bool(args.chat):
        ap.error("give a brief file or --chat NAME")

    if args.chat:
        brief, name = chat(args.chat), args.name or args.chat
    else:
        with open(args.brief) as f:
            brief = Brief.model_validate(json.load(f))
        name = args.name or os.path.basename(args.brief).split(".")[0]
    brief = brief.with_defaults()
    runner = ManualRunner(DEFAULT_WORKDIR) if args.manual else StaadRunner()
    t = time.time()
    manifest, building = run_pipeline(brief, name, runner, pick=args.pick, ask=ask if args.approve else None,
                                      top=args.top, restarts=args.restarts, seed=args.seed, attempts=args.attempts,
                                      rounds=args.rounds, resume=args.resume)
    with open(manifest) as f:
        run = json.load(f)
    print(f"\n{run['status'].upper()} in {time.time() - t:.0f} s: {os.path.relpath(manifest, ROOT)}")
    if building is None:
        sys.exit(1)
    s = next(x for x in run["stages"] if x["stage"] == "building")
    c = s["counts"]
    print(f"{c['columns']} columns, {c['beams']} beams, {c['footings']} footings, {c['walls']} walls, "
          f"{c['openings']} openings, {c['rooms']} rooms, {c['stairs']} stairs; {c['fixtures']} fixtures, "
          f"{c['pipes']} stacks, {c['tanks']} tank; services checks {s['services_checks']}")
    print("Next:\n" + "\n".join(f"  - {n}" for n in run["next_steps"]))


if __name__ == "__main__":
    main()
