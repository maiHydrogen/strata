"""Check Revit's IFC export against the building JSON it was built from (IfcOpenShell, no LLM).

What is checked, each a pass/fail line for the evaluation:
- counts per element class match the JSON (columns, beams, footings, walls, doors, windows, stairs,
  storeys, spaces); slabs are reported, not compared, because Revit also exports stair landings as slabs
- every door and window fills an opening that voids a wall
- every physical element sits in a storey (directly, or through the stair or element it is part of)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import ifcopenshell

# JSON key (or callable) -> IFC class
COUNTED = {
    "IfcColumn": lambda b: len(b.get("columns", [])),
    "IfcBeam": lambda b: len(b.get("beams", [])),
    "IfcFooting": lambda b: len(b.get("footings", [])),
    "IfcWall": lambda b: len(b.get("walls", [])),
    "IfcDoor": lambda b: sum(o["kind"] == "door" for o in b.get("openings", [])),
    "IfcWindow": lambda b: sum(o["kind"] == "window" for o in b.get("openings", [])),
    "IfcStair": lambda b: len(b.get("stairs", [])),
    "IfcBuildingStorey": lambda b: len(b.get("levels", [])),
    "IfcSpace": lambda b: len(b.get("rooms", [])),
}
STRUCTURAL_OR_SPACE = {"IfcColumn", "IfcBeam", "IfcFooting", "IfcWall", "IfcWallStandardCase", "IfcSlab",
                       "IfcDoor", "IfcWindow", "IfcStair", "IfcStairFlight", "IfcSpace", "IfcBuildingStorey",
                       "IfcBuilding", "IfcSite", "IfcOpeningElement", "IfcRailing"}
PHYSICAL = ("IfcColumn", "IfcBeam", "IfcFooting", "IfcWall", "IfcDoor", "IfcWindow", "IfcSlab", "IfcStair")


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class IfcReport:
    schema: str
    checks: list[Check] = field(default_factory=list)
    info: dict = field(default_factory=dict)

    @property
    def passed(self):
        return all(c.passed for c in self.checks)

    def lines(self):
        out = [f"IFC schema {self.schema}: {'PASS' if self.passed else 'FAIL'} "
               f"({sum(c.passed for c in self.checks)}/{len(self.checks)} checks)"]
        out += [f"  [{'ok' if c.passed else 'XX'}] {c.name}: {c.detail}" for c in self.checks]
        out += [f"  info {k}: {v}" for k, v in self.info.items()]
        return out


def _count(model, cls):
    """Instances of exactly this class and its subtypes (IfcWall includes IfcWallStandardCase in IFC2x3)."""
    try:
        return len(model.by_type(cls))
    except RuntimeError:  # class absent from this schema
        return 0


def _storey_of(el, depth=0):
    """The storey an element is contained in, following aggregation (stair flight -> stair) and
    nesting; None if it hangs loose."""
    if depth > 5:
        return None
    for rel in getattr(el, "ContainedInStructure", None) or []:
        if rel.RelatingStructure.is_a("IfcBuildingStorey"):
            return rel.RelatingStructure
    for rel in list(getattr(el, "Decomposes", None) or ()) + list(getattr(el, "Nests", None) or ()):
        parent = rel.RelatingObject
        if parent.is_a("IfcBuildingStorey"):
            return parent
        found = _storey_of(parent, depth + 1)
        if found is not None:
            return found
    return None


def _hosted_in_wall(el):
    for rel in getattr(el, "FillsVoids", None) or []:
        opening = rel.RelatingOpeningElement
        for v in getattr(opening, "VoidsElements", None) or []:
            if v.RelatingBuildingElement.is_a("IfcWall"):
                return True
    return False


def check_ifc(ifc_path: str, building: dict) -> IfcReport:
    model = ifcopenshell.open(ifc_path)
    rep = IfcReport(schema=model.schema)

    for cls, expected in COUNTED.items():
        want, got = expected(building), _count(model, cls)
        rep.checks.append(Check(f"{cls} count", got == want, f"{got} in IFC, {want} in the building JSON"))

    fillers = [e for cls in ("IfcDoor", "IfcWindow") for e in model.by_type(cls)]
    loose = [e.Name or e.GlobalId for e in fillers if not _hosted_in_wall(e)]
    rep.checks.append(Check("doors and windows fill a wall opening", not loose,
                            f"{len(fillers) - len(loose)} of {len(fillers)}" + (f"; not hosted: {loose[:5]}" if loose else "")))

    physical = [e for cls in PHYSICAL for e in model.by_type(cls)]
    orphans = [f"{e.is_a()} {e.Name or e.GlobalId}" for e in physical if _storey_of(e) is None]
    rep.checks.append(Check("every element sits in a storey", not orphans,
                            f"{len(physical) - len(orphans)} of {len(physical)}" + (f"; orphans: {orphans[:5]}" if orphans else "")))

    storeys = sorted(model.by_type("IfcBuildingStorey"), key=lambda s: s.Elevation or 0.0)
    rep.info["storeys"] = ", ".join(f"{s.Name} @ {s.Elevation:.3f}" if s.Elevation is not None else s.Name
                                    for s in storeys)
    fins = sum(1 for f in building.get("finishes", []) if f.get("finish_floor"))
    rep.info["slabs"] = f"{_count(model, 'IfcSlab')} (JSON floors {len(building.get('floors', []))}, " \
                        f"finish floors {fins}, plus stair landings)"
    _check_services(model, building, rep)
    rep.info["stair flights"] = _count(model, "IfcStairFlight")
    base = [s for s in model.by_type("IfcSlab") if s.PredefinedType == "BASESLAB"]
    if base:
        rep.info["base slabs"] = f"{len(base)} (footings exported as slabs if IfcFooting count is short)"
    proxies = {}
    for p in model.by_type("IfcBuildingElementProxy"):
        key = (p.Name or "?").split(":")[0]
        proxies[key] = proxies.get(key, 0) + 1
    if proxies:
        rep.info["proxies"] = ", ".join(f"{k} x{v}" for k, v in sorted(proxies.items()))
    return rep


def _text(el):
    """Everything an element says about itself: attributes and property values (the builder puts the
    STRATA id in Mark and Comments, which the exporter writes to a property set or Tag)."""
    parts = [str(getattr(el, a, "") or "") for a in ("Name", "Description", "ObjectType", "Tag")]
    try:
        import ifcopenshell.util.element as ue
        for props in ue.get_psets(el).values():
            parts += [str(v) for v in props.values()]
    except Exception:  # noqa: BLE001 - a malformed pset must not stop the check
        pass
    return " ".join(parts)


def _check_services(model, building, rep):
    """Phase 6 elements. Fixtures and the tank are found by name ("STRATA <kind>", the builder's DirectShape
    name; newer builds add the id) and counted per kind, because IFC2x3 Coordination View drops Revit's Mark
    and Comments; stacks are the pipe segments. They should export as typed MEP elements, not proxies."""
    fixtures, pipes, tanks = building.get("fixtures", []), building.get("pipes", []), building.get("tanks", [])
    if not (fixtures or pipes or tanks):
        return
    named = {}
    for el in model.by_type("IfcProduct"):
        if el.is_a() in STRUCTURAL_OR_SPACE:
            continue
        name = (el.Name or "").split(":")[0]
        if name.startswith("STRATA "):
            kind = name[len("STRATA "):].split(" L")[0].split(" T")[0].strip()
            named.setdefault(kind, []).append(el)
    classes = {}
    for system in ("plumbing", "electrical", "lighting"):
        want = {}
        for f in fixtures:
            if f["system"] == system:
                want[f["kind"]] = want.get(f["kind"], 0) + 1
        if not want:
            continue
        got = {k: len(named.get(k, [])) for k in want}
        short = {k: f"{got[k]}/{n}" for k, n in want.items() if got[k] != n}
        rep.checks.append(Check(f"{system} fixtures in the IFC", not short,
                                f"{sum(min(got[k], n) for k, n in want.items())} of {sum(want.values())}"
                                + (f"; short: {short}" if short else "")))
        for k in want:
            for el in named.get(k, []):
                classes[el.is_a()] = classes.get(el.is_a(), 0) + 1
    if tanks:
        got = len(named.get("overhead tank", []))
        rep.checks.append(Check("overhead tank in the IFC", got == len(tanks), f"{got} of {len(tanks)}"))
        for el in named.get("overhead tank", []):
            classes[el.is_a()] = classes.get(el.is_a(), 0) + 1
    if pipes:
        segs = [e for cls in ("IfcPipeSegment", "IfcFlowSegment") for e in _by_type(model, cls)]
        segs = list({e.id(): e for e in segs}.values())
        shapes = named.get("soil stack", []) + named.get("waste stack", [])
        got = len(segs) + len(shapes)
        rep.checks.append(Check("stacks in the IFC", got == len(pipes), f"{got} pipe segments, {len(pipes)} stacks"))
    proxies = classes.get("IfcBuildingElementProxy", 0)
    if classes:
        rep.checks.append(Check("services exported as typed elements, not proxies", proxies == 0,
                                f"{sum(classes.values()) - proxies} typed, {proxies} proxies"))
        rep.info["service classes"] = ", ".join(f"{k} x{v}" for k, v in sorted(classes.items()))


def _by_type(model, cls):
    try:
        return model.by_type(cls)
    except RuntimeError:
        return []


def check_files(ifc_path: str, building_path: str) -> IfcReport:
    with open(building_path) as f:
        return check_ifc(ifc_path, json.load(f))
