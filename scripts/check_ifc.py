"""Check Revit's IFC export against its building JSON.

Run: python scripts/check_ifc.py output/structure/g1_40x60_1/g1_40x60_1.building.json [path/to/model.ifc]
Without the IFC path it looks for <name>.ifc beside the JSON, then in the Windows STRATA folder, where
the Revit build exports it. Exit code 1 if any check fails.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.execution.ifc_check import check_files  # noqa: E402

WINDOWS_STRATA = "/mnt/c/Users/Public/Documents/STRATA"


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    bjson = sys.argv[1]
    name = os.path.basename(bjson).replace(".building.json", "")
    ifc = sys.argv[2] if len(sys.argv) > 2 else next(
        (p for p in (os.path.join(os.path.dirname(bjson), name + ".ifc"), os.path.join(WINDOWS_STRATA, name + ".ifc"))
         if os.path.exists(p)), None)
    if not ifc:
        sys.exit(f"No {name}.ifc beside the JSON or in {WINDOWS_STRATA}; run the Revit build first.")
    rep = check_files(ifc, bjson)
    print(f"{ifc}\n" + "\n".join(rep.lines()))
    sys.exit(0 if rep.passed else 1)


if __name__ == "__main__":
    main()
