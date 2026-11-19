"""Build the native Revit model and IFC for a building JSON through the STRATA pyRevit listener, then check the IFC.

Run: python scripts/build_revit.py <name>.building.json     (a file in C:\\Users\\Public\\Documents\\STRATA)
Needs Revit 2027 open with pyRevit Routes on and STRATA_template.rte saved (docs/demo_runbook.md, part A).
Writes <name>.rvt and <name>.ifc beside the JSON. No LLM is involved.
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import webapi as W  # noqa: E402


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    name = os.path.basename(sys.argv[1])
    status = W.revit_status()
    if not status.get("ok"):
        sys.exit(f"Revit is not reachable: {status.get('error')}")
    if not status.get("template"):
        sys.exit(f"STRATA template missing: {status.get('template_path')}")
    jid = W.start_revit(name)
    while (st := W.job_status(jid))["state"] in ("queued", "running"):
        print(f"\r{st.get('stage')} {st['elapsed']:.0f} s", end="", flush=True)
        time.sleep(2)
    print()
    if st["state"] != "complete":
        print(st.get("text") or "")
        sys.exit(f"Revit build failed: {st.get('reason')}")
    print(st.get("text") or "")
    print(f"\nBuilt in {st['seconds']} s: {st['files']}")
    check = st.get("ifc_check")
    if check:
        print(f"IFC check ({check['schema']}): {sum(c['passed'] for c in check['checks'])}/{len(check['checks'])} pass")
        for c in check["checks"]:
            print(f"  [{'ok' if c['passed'] else 'XX'}] {c['name']}: {c['detail']}")
    sys.exit(0 if check and check["passed"] else 1)


if __name__ == "__main__":
    main()
