"""Pack a finished pipeline run into the web app's demo file, shown where STAAD is not reachable.

Run: python scripts/make_web_demo.py [output/runs/<name>]   (default: output/runs/web_demo)
Make the run first:  python scripts/strata.py examples/g1_40x60.brief.json --name web_demo
Writes web/public/demo/run.json: {brief, layout {name, svg}, result} in the shape /api/structure returns.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.webapi import result_view  # noqa: E402


def main():
    run_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "output", "runs", "web_demo")
    with open(os.path.join(run_dir, "run.json")) as f:
        run = json.load(f)
    if run["status"] != "complete":
        sys.exit(f"{run_dir}: run is {run['status']}, not complete")
    files = {k: os.path.join(ROOT, v) for k, v in run["files"].items()}
    building = json.load(open(files["building"]))
    structure = next(s for s in run["stages"] if s["stage"] == "structure")
    approval = next(s for s in run["stages"] if s["stage"] == "approval")
    svg = open(files[f"svg_{approval['option']}"]).read()
    result = result_view(run_dir, building, structure, run["layout"])
    demo = {"name": run["name"], "brief": json.load(open(files["brief"])),
            "layout": {"name": run["layout"], "svg": svg}, "result": result,
            "rounds": [{"layout": run["layout"], "number": i + 1, "sections": None, "passed": None}
                       for i in range(structure["attempts"][-1]["rounds"])] if structure.get("attempts") else []}
    out = os.path.join(ROOT, "web", "public", "demo", "run.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(demo, f, separators=(",", ":"), default=str)
    print(f"Wrote {out} ({os.path.getsize(out) // 1024} KB) from {run_dir}")


if __name__ == "__main__":
    main()
