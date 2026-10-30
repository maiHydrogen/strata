"""G+1 house spike: STAAD design loop -> building JSON -> ready for the Revit builder.

Run: python scripts/g1_house_spike.py            (runs STAAD automatically)
     python scripts/g1_house_spike.py --manual   (writes .std files and waits for you to run them)

Writes output/g1_house.building.json and copies it to the Windows STRATA folder.
Simplification: STAAD loads every beam with a 230 mm wall, conservative for 115 mm partitions.
"""

import argparse
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.execution.building_schema import Building, validate_building  # noqa: E402
from src.execution.frame_to_building import structure_from_frame  # noqa: E402
from src.execution.sample_house import architecture, house_frame  # noqa: E402
from src.structure.design_loop import design  # noqa: E402
from src.structure.staad_runner import DEFAULT_WORKDIR, ManualRunner, StaadRunner  # noqa: E402

OUT = os.path.join(ROOT, "output", "g1_house.building.json")
CODE_NOTE = "IS 456:2000 + IS 13920:2016, seismic IS 1893 Zone V"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manual", action="store_true", help="run STAAD yourself instead of automatically")
    args = parser.parse_args()

    runner = ManualRunner(DEFAULT_WORKDIR) if args.manual else StaadRunner()
    outcome = design(house_frame(), runner, name="g1_house", max_rounds=8)
    for r in outcome.rounds:
        print(f"round {r.number}: {r.sections} bars {r.bars} {'PASS' if r.passed else 'FAIL'}")
        for change in r.changes:
            print(f"    {change}")
    if not outcome.passed:
        sys.exit(f"Structural design did not pass: {outcome.reason}")

    building = {"schema_version": "0.2", "name": "STRATA G+1 test house"}
    building.update(structure_from_frame(outcome.frame, outcome.results, CODE_NOTE))
    building.update(architecture(outcome.frame))
    problems = validate_building(Building.model_validate(building))
    if problems:
        sys.exit("Building JSON failed validation:\n  " + "\n  ".join(problems))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(building, f, indent=2)
    shutil.copy(OUT, os.path.join(DEFAULT_WORKDIR, os.path.basename(OUT)))
    print(f"{len(building['columns'])} columns, {len(building['beams'])} beams, {len(building['walls'])} walls, "
          f"{len(building['openings'])} openings, {len(building['rooms'])} rooms")
    print(f"Wrote {OUT} and copied it to {DEFAULT_WORKDIR}")


if __name__ == "__main__":
    main()
