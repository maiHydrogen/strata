"""Brief -> spec: python scripts/make_spec.py examples/g1_house.brief.json

Writes output/<name>.spec.json and prints what was defaulted, unresolved or deferred.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.brief.schema import Brief  # noqa: E402
from src.inference.spec import build_spec  # noqa: E402


def main():
    path = sys.argv[1]
    with open(path) as f:
        brief = Brief.model_validate_json(f.read())
    missing = brief.missing_must()
    spec = build_spec(brief)
    name = os.path.basename(path).removesuffix(".brief.json").removesuffix(".json")
    out = os.path.join(ROOT, "output", f"{name}.spec.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(spec, f, indent=2)
    print(f"Must items the client has not answered: {missing or 'none'}")
    print("Defaulted:", ", ".join(f"{k} {v[len('default '):]}" for k, v in spec["brief_sources"].items()
                                  if v.startswith("default")))
    for c in spec["checks"]:
        print(f"{'PASS' if c['pass'] else 'FAIL'} {c['check']}: {c['actual']}")
    for u in spec["unresolved"]:
        print(f"unresolved {u['element']}.{u['quantity']}: {u['reason']}")
    for d in spec["deferred"]:
        print(f"deferred {d['element']}.{d['quantity']}: needs {', '.join(d['needs'])}")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
