"""Check Revit's IFC export against the building JSON it was built from (IfcOpenShell, no LLM).

What is checked, each a pass/fail line for the evaluation:
- counts per element class match the JSON (columns, beams, footings, walls, doors, windows, stairs,
  storeys, spaces); slabs are reported, not compared, because Revit also exports stair landings as slabs
- every door and window fills an opening that voids a wall
- every physical element sits in a storey (directly, or through the stair or element it is part of)
- Revit vs STAAD: every column, beam and footing is found by its member id (Revit Mark), has the section
  STAAD gave it and sits where the STAAD frame put it (within 10 mm), and carries its STAAD result in the
  STRATA parameters. The building JSON's members are the STAAD model's final design round, so matching
  them is matching the STAAD model.
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
    _check_members(model, building, rep)
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


POS_TOL = 0.010   # m: member positions, Revit against the STAAD frame
SIZE_TOL = 0.002  # m: section and pad sizes
MEMBERS = (("columns", "IfcColumn"), ("beams", "IfcBeam"), ("footings", "IfcFooting"))


def _flat_props(el):
    import ifcopenshell.util.element as ue
    out = {}
    try:
        for props in ue.get_psets(el).values():
            out.update(props)
    except Exception:  # noqa: BLE001
        pass
    return out


def _bounds(settings, el):
    """World-coordinate bounding box of an element's body in metres, or None."""
    import ifcopenshell.geom
    try:
        shape = ifcopenshell.geom.create_shape(settings, el)  # keep the shape alive while its geometry is read
        verts = list(shape.geometry.verts)
    except Exception:  # noqa: BLE001
        return None
    if not verts:
        return None
    xs, ys, zs = verts[0::3], verts[1::3], verts[2::3]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def _size_problem(kind, spec, props, scale):
    def got(*names):
        for n in names:
            if props.get(n) is not None:
                return float(props[n]) * scale
        return None
    if kind == "footings":
        pairs = [(spec["width"], got("Width")), (spec["length"], got("Length")),
                 (spec["thickness"], got("Foundation Thickness", "Thickness"))]
    else:
        b, h = got("b"), got("h")
        if kind == "columns" and None not in (b, h):  # orientation free: compare the sorted pair
            pairs = list(zip(sorted((spec["width"], spec["depth"])), sorted((b, h))))
        else:
            pairs = [(spec["width"], b), (spec["depth"], h)]
    if any(g is None for _, g in pairs):
        return "size not in the IFC"
    if any(abs(w - g) > SIZE_TOL for w, g in pairs):
        return "size {0} mm, STAAD {1} mm".format("x".join(f"{g * 1000:.0f}" for _, g in pairs),
                                                  "x".join(f"{w * 1000:.0f}" for w, _ in pairs))
    return None


def _position_problem(kind, spec, el, box, elev, scale):
    if kind == "footings":
        import ifcopenshell.util.placement as up
        m = up.get_local_placement(el.ObjectPlacement)
        x, y = m[0][3] * scale, m[1][3] * scale
        d = max(abs(x - spec["at"][0]), abs(y - spec["at"][1]))
        return None if d <= POS_TOL else f"at ({x:.3f}, {y:.3f}), STAAD ({spec['at'][0]}, {spec['at'][1]})"
    if box is None:
        return "no geometry"
    (x0, y0, z0), (x1, y1, z1) = box
    if kind == "columns":
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        zb, zt = elev[spec["base_level"]], elev[spec["top_level"]]
        bad = []
        if max(abs(cx - spec["at"][0]), abs(cy - spec["at"][1])) > POS_TOL:
            bad.append(f"centre ({cx:.3f}, {cy:.3f}), STAAD ({spec['at'][0]}, {spec['at'][1]})")
        if abs(z0 - zb) > POS_TOL or abs(z1 - zt) > POS_TOL:
            bad.append(f"z {z0:.3f} to {z1:.3f}, STAAD {zb} to {zt}")
        return "; ".join(bad) or None
    (sx, sy), (ex, ey) = spec["start"], spec["end"]
    top = elev[spec["level"]]
    bad = []
    if abs(z1 - top) > POS_TOL:
        bad.append(f"top {z1:.3f}, level {top}")
    if abs(sy - ey) <= 1e-6:  # along x: centred on the line, inside its span (Revit trims at the columns)
        if abs((y0 + y1) / 2 - sy) > POS_TOL or x0 < min(sx, ex) - POS_TOL or x1 > max(sx, ex) + POS_TOL:
            bad.append(f"x {x0:.3f}-{x1:.3f}, y {(y0 + y1) / 2:.3f}; STAAD line ({sx}, {sy})-({ex}, {ey})")
    elif abs(sx - ex) <= 1e-6:
        if abs((x0 + x1) / 2 - sx) > POS_TOL or y0 < min(sy, ey) - POS_TOL or y1 > max(sy, ey) + POS_TOL:
            bad.append(f"y {y0:.3f}-{y1:.3f}, x {(x0 + x1) / 2:.3f}; STAAD line ({sx}, {sy})-({ex}, {ey})")
    else:  # sloping in plan: the box centre lies on the line
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        dx, dy = ex - sx, ey - sy
        dist = abs(dy * (cx - sx) - dx * (cy - sy)) / (dx * dx + dy * dy) ** 0.5
        if dist > POS_TOL:
            bad.append(f"centre {dist * 1000:.0f} mm off the STAAD line")
    return "; ".join(bad) or None


def _check_members(model, building, rep):
    """Revit against STAAD, through the IFC: id, section, position and STAAD result of every member."""
    import ifcopenshell.geom
    import ifcopenshell.util.unit as uu
    specs = {kind: [m for m in building.get(kind, []) if m.get("id")] for kind, _ in MEMBERS}
    total = sum(len(v) for v in specs.values())
    if not total:  # a hand-made building without member ids: nothing to match
        return
    scale = uu.calculate_unit_scale(model)
    elev = {lv["id"]: lv.get("elevation", 0.0) for lv in building.get("levels", [])}
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    missing, sizes, places, results = [], [], [], []
    for kind, cls in MEMBERS:
        by_id = {}
        for el in _by_type(model, cls):
            props = _flat_props(el)
            mid = props.get("STRATA Member") or props.get("Mark")
            if mid:
                by_id[str(mid)] = (el, props)
        for spec in specs[kind]:
            found = by_id.get(spec["id"])
            if found is None:
                missing.append(spec["id"])
                continue
            el, props = found
            problem = _size_problem(kind, spec, props, scale)
            if problem:
                sizes.append(f"{spec['id']}: {problem}")
            box = None if kind == "footings" else _bounds(settings, el)
            problem = _position_problem(kind, spec, el, box, elev, scale)
            if problem:
                places.append(f"{spec['id']}: {problem}")
            design = spec.get("design") or {}
            want_status, want_util = design.get("status"), design.get("utilisation")
            got_status, got_util = props.get("STRATA Design Status"), props.get("STRATA Utilisation")
            if want_status and (got_status != want_status or (want_util is not None and (
                    got_util is None or abs(float(got_util) - float(want_util)) > 0.001))):
                results.append(f"{spec['id']}: {got_status}/{got_util}, JSON {want_status}/{want_util}")
    found = total - len(missing)
    rep.checks.append(Check("structural members found by STAAD member id", not missing,
                            f"{found} of {total}" + (f"; missing: {missing[:6]}" if missing else "")))
    rep.checks.append(Check("member sections match the STAAD design", bool(found) and not sizes,
                            f"{found - len(sizes)} of {found}" + (f"; {sizes[:3]}" if sizes else "")))
    rep.checks.append(Check("member positions match the STAAD frame (10 mm)", bool(found) and not places,
                            f"{found - len(places)} of {found}" + (f"; {places[:3]}" if places else "")))
    rep.checks.append(Check("STAAD results on every member (STRATA parameters)", bool(found) and not results,
                            f"{found - len(results)} of {found}" + (f"; {results[:3]}" if results else "")))


def _by_type(model, cls):
    try:
        return model.by_type(cls)
    except RuntimeError:
        return []


def check_files(ifc_path: str, building_path: str) -> IfcReport:
    with open(building_path) as f:
        return check_ifc(ifc_path, json.load(f))
