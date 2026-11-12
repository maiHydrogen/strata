"""Approved layout -> STAAD-designed frame -> footings -> building JSON for Revit.

Run: python scripts/design_structure.py output/layouts/g1_40x60_1.layout.json --brief examples/g1_40x60.brief.json
     [--manual]    write each .std and wait for you to run it in STAAD.Pro
     [--std-only]  write the first-round .std and the load notes, run nothing

The brief gives the knowledge-graph values (imposed loads, seismic zone, wind speed, soil
bearing capacity). If the frame cannot be made to pass, the failure goes back to the layout
GA as a shorter maximum span and the new layout is designed again (up to --attempts times).

Writes output/structure/<name>/: <name>.std (last round), design.json (rounds, sizes, notes,
footings), <name>.building.json, plus the new layout when the GA was rerun; the building JSON
is also copied to the Windows STRATA folder for the Revit builder.
"""

import argparse
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.brief.schema import Brief  # noqa: E402
from src.execution.building_schema import Building, validate_building  # noqa: E402
from src.execution.layout_to_building import building_from_layout  # noqa: E402
from src.inference.spec import build_spec  # noqa: E402
from src.spatial.checker import check_layout  # noqa: E402
from src.spatial.ga import alternatives, evaluate, to_layout  # noqa: E402
from src.spatial.layout import Layout  # noqa: E402
from src.structure.from_layout import build_frame  # noqa: E402
from src.structure.pipeline import design_structure, layout_feedback, summary  # noqa: E402
from src.structure.staad_runner import DEFAULT_WORKDIR, ManualRunner, StaadRunner  # noqa: E402
from src.structure.std_writer import write_std  # noqa: E402

CODE_NOTE = "IS 456:2000 + IS 13920:2016; loads IS 875 Parts 1-3, seismic IS 1893 (Part 1):2016"


def relayout(brief, spec, max_span, name, seed=1):
    """Best layout the GA finds with a shorter maximum span, or None if none passes the checker."""
    genomes, p = alternatives(brief, spec, count=3, restarts=6, seed=seed, max_span=max_span)
    for k, g in enumerate(genomes, start=1):
        if evaluate(g, p, detail=True)[0] > 0:
            continue
        layout = to_layout(g, p, name=f"{name}_span{max_span:g}_{k}")
        if check_layout(layout, spec)["pass"]:
            return layout
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("layout")
    ap.add_argument("--brief", required=True)
    ap.add_argument("--manual", action="store_true")
    ap.add_argument("--std-only", action="store_true")
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--attempts", type=int, default=3)
    args = ap.parse_args()

    with open(args.brief) as f:
        brief = Brief.model_validate(json.load(f)).with_defaults()
    spec = build_spec(brief)
    with open(args.layout) as f:
        layout = Layout.model_validate(json.load(f))
    name = layout.name
    out = os.path.join(ROOT, "output", "structure", name)
    os.makedirs(out, exist_ok=True)

    if args.std_only:
        model = build_frame(layout, spec)
        path = os.path.join(out, f"{name}.std")
        with open(path, "w") as f:
            f.write(write_std(model.frame, title=name))
        print("\n".join(f"- {n}" for n in model.notes))
        print(f"Wrote {path}")
        return

    runner = ManualRunner(DEFAULT_WORKDIR) if args.manual else StaadRunner()
    for attempt in range(1, args.attempts + 1):
        result = design_structure(layout, spec, runner, layout.name, brief=brief, max_rounds=args.rounds)
        for r in result.outcome.rounds:
            print(f"round {r.number}: {r.sections} bars {r.bars} {'PASS' if r.passed else 'FAIL'}")
            for c in r.changes:
                print(f"    {c}")
        if result.passed:
            break
        fb = layout_feedback(result)
        print(f"Frame did not pass: {result.outcome.reason}")
        if fb is None:
            sys.exit("No layout change left that would help; the brief needs a bigger plot or fewer rooms.")
        print(f"Back to the layout stage: {fb['reason']}")
        new = relayout(brief, spec, fb["max_span"], name)
        if new is None:
            sys.exit(f"The layout GA found no passing plan with spans up to {fb['max_span']} m.")
        layout = new
        with open(os.path.join(out, f"{layout.name}.layout.json"), "w") as f:
            f.write(layout.model_dump_json(indent=1))
    else:
        sys.exit(f"Structure did not pass after {args.attempts} layouts.")

    std_path = os.path.join(out, f"{layout.name}.std")
    with open(std_path, "w") as f:
        f.write(write_std(result.outcome.frame, title=layout.name))
    info = summary(result)
    info["layout"] = layout.name
    with open(os.path.join(out, "design.json"), "w") as f:
        json.dump(info, f, indent=1)
    building = building_from_layout(layout, result.model, result.outcome, result.footings, CODE_NOTE,
                                    name=f"STRATA {layout.name}")
    problems = validate_building(Building.model_validate(building))
    if problems:
        sys.exit("Building JSON failed validation:\n  " + "\n  ".join(problems))
    bpath = os.path.join(out, f"{layout.name}.building.json")
    with open(bpath, "w") as f:
        json.dump(building, f, indent=1)
    if os.path.isdir(DEFAULT_WORKDIR):
        shutil.copy(bpath, os.path.join(DEFAULT_WORKDIR, os.path.basename(bpath)))
    print("\n".join(f"- {n}" for n in result.notes))
    sizes = ", ".join(f"{g} {s}" for g, s in info["sections"].items())
    print(f"PASS in {len(result.outcome.rounds)} rounds: {sizes}; slab {info['slab_mm']} mm")
    print("Footings (m): " + ", ".join(f"{f.width:g}x{f.length:g}x{f.thickness:g}" for f in result.footings))
    print(f"{len(building['columns'])} columns, {len(building['beams'])} beams, {len(building['footings'])} footings, "
          f"{len(building['walls'])} walls, {len(building['openings'])} openings, {len(building['rooms'])} rooms")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
