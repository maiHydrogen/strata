# -*- coding: utf-8 -*-
"""STRATA deterministic Revit builder: building JSON -> native Revit elements.

Runs inside Revit (Dynamo Python node or pyRevit). Uses only the Revit API,
no Dynamo/pyRevit calls, so both launchers share it. The caller owns the
transaction (Dynamo manages its own). Written for IronPython 2.7 and
CPython 3 alike: no f-strings, no type hints.

The JSON is validated in WSL (src/execution/building_schema.py) before it
gets here; this module only translates it. No LLM is involved.
"""

import json

import clr

clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (  # noqa: E402
    BuiltInCategory, BuiltInParameter, Color, CompoundStructure, CompoundStructureLayer,
    CurveLoop, Element, ElementTypeGroup, FamilySymbol, FilteredElementCollector, Floor,
    FloorType, Level, Line, Material, MaterialFunctionAssignment, ShellLayerType, UnitTypeId,
    UnitUtils, UV, Wall, WallKind, WallType, XYZ,
)
from Autodesk.Revit.DB.Structure import StructuralType  # noqa: E402
from System.Collections.Generic import List  # noqa: E402

FUNCTIONS = {
    "Structure": MaterialFunctionAssignment.Structure,
    "Substrate": MaterialFunctionAssignment.Substrate,
    "Finish1": MaterialFunctionAssignment.Finish1,
    "Finish2": MaterialFunctionAssignment.Finish2,
    "Membrane": MaterialFunctionAssignment.Membrane,
}
MATERIAL_COLORS = {"Brick": (178, 102, 72), "Plaster": (232, 230, 222), "Concrete": (180, 180, 180)}
WALL_CENTERLINE = 0  # WALL_KEY_REF_PARAM value for "Wall Centerline"
# (kind, on an external wall) -> family names tried in order; else the first loaded family
PREFERRED_FAMILIES = {
    ("door", True): ("M_Door-Exterior-Single-Two_Lite", "Door-Exterior-Single-Two_Lite"),
    ("door", False): ("M_Door-Passage-Single-Flush", "Door-Passage-Single-Flush", "M_Single-Flush"),
    ("window", True): ("M_Window-Casement-Double", "Window-Casement-Double"),
    ("window", False): ("M_Window-Fixed", "Window-Fixed"),
}


def ft(metres):
    return UnitUtils.ConvertToInternalUnits(float(metres), UnitTypeId.Meters)


def m2(sq_feet):
    return UnitUtils.ConvertFromInternalUnits(sq_feet, UnitTypeId.SquareMeters)


def _name(element):
    """Element name; IronPython needs Element.Name.GetValue for ElementType subclasses."""
    try:
        return element.Name
    except AttributeError:
        return Element.Name.GetValue(element)


def _by_name(doc, cls, name):
    for e in FilteredElementCollector(doc).OfClass(cls):
        if _name(e) == name:
            return e
    return None


class Builder(object):
    def __init__(self, doc, data):
        self.doc = doc
        self.data = data
        self.levels = {}
        self.wall_types = {}
        self.walls = {}
        self.materials = {}
        self.placed = []  # (opening spec, instance, host wall) for the diagnostics
        self.cut_info = {}  # opening id -> (placement method, wall volume removed m3, expected m3)
        self.report = {"levels": 0, "wall_types": 0, "walls": 0, "doors": 0, "windows": 0,
                       "floors": 0, "rooms": [], "openings": [], "warnings": []}

    def warn(self, msg):
        self.report["warnings"].append(msg)

    # ---- types and materials ------------------------------------------------

    def material(self, name):
        if name not in self.materials:
            mat = _by_name(self.doc, Material, name)
            if mat is None:
                mat = self.doc.GetElement(Material.Create(self.doc, name))
                if name in MATERIAL_COLORS:
                    mat.Color = Color(*MATERIAL_COLORS[name])
            self.materials[name] = mat.Id
        return self.materials[name]

    def compound(self, layers, base_type=None):
        """Layered structure. With base_type, its own structure is reused and only the
        layers replaced, keeping type-specific settings (floors reject wall end caps)."""
        cs_layers = List[CompoundStructureLayer]()
        for layer in layers:
            cs_layers.Add(CompoundStructureLayer(ft(layer["thickness"]), FUNCTIONS[layer["function"]],
                                                 self.material(layer["material"])))
        cs = base_type.GetCompoundStructure() if base_type is not None else None
        if cs is not None:
            cs.SetLayers(cs_layers)
        else:
            cs = CompoundStructure.CreateSimpleCompoundStructure(cs_layers)
        # Layers before/after the first/last Structure layer are the exterior/interior shells
        structural = [i for i, layer in enumerate(layers) if layer["function"] == "Structure"]
        if structural:
            cs.SetNumberOfShellLayers(ShellLayerType.Exterior, structural[0])
            cs.SetNumberOfShellLayers(ShellLayerType.Interior, len(layers) - 1 - structural[-1])
            cs.StructuralMaterialIndex = structural[0]
        return cs

    def make_wall_types(self):
        base = None
        for wt in FilteredElementCollector(self.doc).OfClass(WallType):
            if wt.Kind == WallKind.Basic:
                base = wt
                break
        for spec in self.data["wall_types"]:
            wt = _by_name(self.doc, WallType, spec["name"])
            if wt is None:
                wt = self.doc.GetElement(base.Duplicate(spec["name"]).Id)
                self.report["wall_types"] += 1
            wt.SetCompoundStructure(self.compound(spec["layers"]))
            self.wall_types[spec["id"]] = wt

    def floor_type(self, thickness, material):
        name = "STRATA Slab {0:.0f} {1}".format(thickness * 1000, material)
        ftype = _by_name(self.doc, FloorType, name)
        if ftype is None:
            base = self.doc.GetElement(self.doc.GetDefaultElementTypeId(ElementTypeGroup.FloorType))
            ftype = self.doc.GetElement(base.Duplicate(name).Id)
        ftype.SetCompoundStructure(self.compound(
            [{"material": material, "thickness": thickness, "function": "Structure"}], base_type=ftype))
        return ftype

    def opening_type(self, kind, width, height, external):
        """A door/window type of exactly width x height, duplicated from the preferred family
        for its wall (exterior door for external walls, passage door inside)."""
        category = BuiltInCategory.OST_Doors if kind == "door" else BuiltInCategory.OST_Windows
        all_symbols = list(FilteredElementCollector(self.doc).OfCategory(category).OfClass(FamilySymbol))
        if not all_symbols:
            raise RuntimeError("No {0} family loaded in this project/template".format(kind))
        family = _name(all_symbols[0].Family)
        families = set(_name(s.Family) for s in all_symbols)
        for preferred in PREFERRED_FAMILIES.get((kind, external), ()):
            if preferred in families:
                family = preferred
                break
        symbols = [s for s in all_symbols if _name(s.Family) == family]
        name = "STRATA {0:.0f} x {1:.0f}mm".format(width * 1000, height * 1000)
        sym = None
        for s in symbols:
            if _name(s) == name:
                sym = s
        if sym is None:
            sym = self.doc.GetElement(symbols[0].Duplicate(name).Id)
            w_params = (BuiltInParameter.DOOR_WIDTH, BuiltInParameter.WINDOW_WIDTH, BuiltInParameter.FAMILY_WIDTH_PARAM)
            h_params = (BuiltInParameter.DOOR_HEIGHT, BuiltInParameter.WINDOW_HEIGHT, BuiltInParameter.FAMILY_HEIGHT_PARAM)
            for params, value, label in ((w_params, width, "Width"), (h_params, height, "Height")):
                if not self._set_first(sym, params, label, ft(value)):
                    self.warn("{0}: could not set {1} on type '{2}'".format(kind, label, name))
        if not sym.IsActive:
            sym.Activate()
            self.doc.Regenerate()
        return sym

    @staticmethod
    def _set_first(element, builtins, label, value):
        candidates = [element.get_Parameter(b) for b in builtins] + [element.LookupParameter(label)]
        for p in candidates:
            if p is not None and not p.IsReadOnly:
                p.Set(value)
                return True
        return False

    # ---- elements -----------------------------------------------------------

    def make_levels(self):
        existing = list(FilteredElementCollector(self.doc).OfClass(Level))
        for spec in self.data["levels"]:
            level = None
            for lv in existing:
                if abs(lv.Elevation - ft(spec["elevation"])) < 1e-6:
                    level = lv  # reuse the template's level at this elevation
            if level is None:
                level = Level.Create(self.doc, ft(spec["elevation"]))
                level.Name = spec["name"]
                self.report["levels"] += 1
            self.levels[spec["id"]] = level

    def make_walls(self):
        ext = [w for w in self.data["walls"] if w.get("external")]
        pts = [p for w in ext for p in (w["start"], w["end"])] or [[0, 0]]
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        for spec in self.data["walls"]:
            level = self.levels[spec["level"]]
            (x1, y1), (x2, y2) = spec["start"], spec["end"]
            line = Line.CreateBound(XYZ(ft(x1), ft(y1), level.Elevation), XYZ(ft(x2), ft(y2), level.Elevation))
            wall = Wall.Create(self.doc, line, self.wall_types[spec["type"]].Id, level.Id,
                               ft(spec["height"]), 0.0, False, False)
            wall.get_Parameter(BuiltInParameter.WALL_KEY_REF_PARAM).Set(WALL_CENTERLINE)
            self.walls[spec["id"]] = (wall, spec)
            self.report["walls"] += 1
        # Wall.Orientation needs computed geometry, so regenerate before checking faces
        self.doc.Regenerate()
        for wall, spec in self.walls.values():
            if spec.get("external"):
                # Exterior face must point away from the building centre
                (x1, y1), (x2, y2) = spec["start"], spec["end"]
                mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                o = wall.Orientation
                if o.X * (mx - cx) + o.Y * (my - cy) < 0:
                    wall.Flip()
        self.doc.Regenerate()

    def _wall_volume(self, wall):
        p = wall.get_Parameter(BuiltInParameter.HOST_VOLUME_COMPUTED)
        return UnitUtils.ConvertFromInternalUnits(p.AsDouble(), UnitTypeId.CubicMeters) if p else 0.0

    def _place(self, spec, sym, wall, level, point, method):
        if method == "level":
            inst = self.doc.Create.NewFamilyInstance(point, sym, wall, level, StructuralType.NonStructural)
        else:  # "host": Revit takes the level from the host wall
            inst = self.doc.Create.NewFamilyInstance(point, sym, wall, StructuralType.NonStructural)
        sill = inst.get_Parameter(BuiltInParameter.INSTANCE_SILL_HEIGHT_PARAM)
        if sill is not None and not sill.IsReadOnly:
            sill.Set(ft(spec.get("sill", 0.0)))
        elif spec.get("sill", 0.0) > 0:
            self.warn("{0}: sill height not settable".format(spec["id"]))
        self.doc.Regenerate()
        return inst

    def make_openings(self):
        """Place each opening and confirm it really cuts its wall: the wall's computed volume
        must drop by about width x height x thickness. If not, re-place it with the other
        NewFamilyInstance overload and check again."""
        thickness = dict((t["id"], sum(layer["thickness"] for layer in t["layers"]))
                         for t in self.data["wall_types"])
        for spec in self.data.get("openings", []):
            wall, wspec = self.walls[spec["wall"]]
            level = self.levels[wspec["level"]]
            (x1, y1), (x2, y2) = wspec["start"], wspec["end"]
            length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            t = (spec["offset"] + spec["width"] / 2.0) / length  # family insertion point = opening centre
            point = XYZ(ft(x1 + (x2 - x1) * t), ft(y1 + (y2 - y1) * t), level.Elevation)
            sym = self.opening_type(spec["kind"], spec["width"], spec["height"], bool(wspec.get("external")))
            expected = spec["width"] * spec["height"] * thickness[wspec["type"]]
            before = self._wall_volume(wall)
            inst, removed, method = None, 0.0, None
            # "host" placement cuts reliably; "level" sometimes leaves the first opening in a wall uncut
            for method in ("host", "level"):
                if inst is not None:
                    self.doc.Delete(inst.Id)
                    self.doc.Regenerate()
                inst = self._place(spec, sym, wall, level, point, method)
                removed = before - self._wall_volume(wall)
                if removed >= 0.5 * expected:
                    break
            self.cut_info[spec["id"]] = (method, removed, expected)
            self.report["doors" if spec["kind"] == "door" else "windows"] += 1
            self.placed.append((spec, inst, wall))

    def make_floors(self):
        for spec in self.data.get("floors", []):
            level = self.levels[spec["level"]]
            loop = CurveLoop()
            pts = spec["boundary"]
            for i in range(len(pts)):
                a, b = pts[i], pts[(i + 1) % len(pts)]
                loop.Append(Line.CreateBound(XYZ(ft(a[0]), ft(a[1]), level.Elevation),
                                             XYZ(ft(b[0]), ft(b[1]), level.Elevation)))
            loops = List[CurveLoop]()
            loops.Add(loop)
            ftype = self.floor_type(spec["thickness"], spec.get("material", "Concrete"))
            Floor.Create(self.doc, loops, ftype.Id, level.Id)
            self.report["floors"] += 1
        self.doc.Regenerate()

    def make_rooms(self):
        for spec in self.data.get("rooms", []):
            level = self.levels[spec["level"]]
            room = self.doc.Create.NewRoom(level, UV(ft(spec["point"][0]), ft(spec["point"][1])))
            room.get_Parameter(BuiltInParameter.ROOM_NAME).Set(spec["name"])
            self.doc.Regenerate()
            area = m2(room.Area)
            if area <= 0:
                self.warn("room {0}: not enclosed (area 0)".format(spec["id"]))
            self.report["rooms"].append("{0}: {1:.2f} m2".format(spec["name"], area))

    def check_openings(self):
        """Report what Revit actually placed for each door/window, and whether it cuts its wall."""
        self.doc.Regenerate()
        for spec, inst, wall in self.placed:
            sym = inst.Symbol
            method, removed, expected = self.cut_info[spec["id"]]
            host = inst.Host
            width = self._read_first(sym, (BuiltInParameter.DOOR_WIDTH, BuiltInParameter.WINDOW_WIDTH,
                                           BuiltInParameter.FAMILY_WIDTH_PARAM), "Width")
            height = self._read_first(sym, (BuiltInParameter.DOOR_HEIGHT, BuiltInParameter.WINDOW_HEIGHT,
                                            BuiltInParameter.FAMILY_HEIGHT_PARAM), "Height")
            loc = inst.Location.Point
            line = ("{0}: {1} / {2}, host {3}, size {4} x {5} mm, at ({6:.3f}, {7:.3f}) m, "
                    "cut {8:.3f} of {9:.3f} m3 via '{10}' placement: {11}").format(
                spec["id"], _name(sym.Family), _name(sym),
                "wall " + spec["wall"] if host is not None and host.Id == wall.Id else "NOT HOSTED",
                width, height,
                UnitUtils.ConvertFromInternalUnits(loc.X, UnitTypeId.Meters),
                UnitUtils.ConvertFromInternalUnits(loc.Y, UnitTypeId.Meters),
                removed, expected, method, "OK" if removed >= 0.5 * expected else "NOT CUT")
            self.report["openings"].append(line)
        for kind, category in (("door", BuiltInCategory.OST_Doors), ("window", BuiltInCategory.OST_Windows)):
            families = sorted({_name(s.Family) for s in
                               FilteredElementCollector(self.doc).OfCategory(category).OfClass(FamilySymbol)})
            self.report["openings"].append("Loaded {0} families: {1}".format(kind, ", ".join(families)))

    @staticmethod
    def _read_first(element, builtins, label):
        for p in [element.get_Parameter(b) for b in builtins] + [element.LookupParameter(label)]:
            if p is not None and p.HasValue:
                return "{0:.0f}".format(UnitUtils.ConvertFromInternalUnits(p.AsDouble(), UnitTypeId.Millimeters))
        return "?"

    def build(self):
        self.make_levels()
        self.make_wall_types()
        self.make_walls()
        self.make_openings()
        self.check_openings()
        self.make_floors()
        self.make_rooms()
        return self.report


def build_from_file(doc, path):
    with open(path) as f:
        data = json.load(f)
    return Builder(doc, data).build()


def format_report(report):
    lines = ["STRATA build complete",
             "Levels created: {0}".format(report["levels"]),
             "Wall types created: {0}".format(report["wall_types"]),
             "Walls: {0}  Doors: {1}  Windows: {2}  Floors: {3}".format(
                 report["walls"], report["doors"], report["windows"], report["floors"]),
             "Rooms: " + ", ".join(report["rooms"]),
             "Openings:"] + ["  " + o for o in report.get("openings", [])]
    if report["warnings"]:
        lines.append("Warnings:")
        lines += ["  - " + w for w in report["warnings"]]
    return "\n".join(lines)
