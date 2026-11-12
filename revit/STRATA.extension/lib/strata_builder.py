# -*- coding: utf-8 -*-
"""STRATA deterministic Revit builder: building JSON -> native Revit elements.

Runs inside Revit (Dynamo Python node or pyRevit). Uses only the Revit API,
no Dynamo/pyRevit calls, so both launchers share it. The caller owns the
transaction for build() (Dynamo manages its own); make_stairs() runs after the
caller has committed it, because a StairsEditScope cannot start inside an open
transaction. Written for IronPython 2.7 and CPython 3 alike: no f-strings, no
type hints.

The JSON is validated in WSL (src/execution/building_schema.py) before it
gets here; this module only translates it. No LLM is involved.
"""

import json
import uuid

import clr

clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (  # noqa: E402
    BuiltInCategory, BuiltInParameter, Color, CompoundStructure, CompoundStructureLayer,
    CurveLoop, Element, ElementLevelFilter, ElementTransformUtils, ElementTypeGroup, FailureProcessingResult,
    FailureSeverity, FamilySymbol, FilteredElementCollector, Floor, FloorType, IFailuresPreprocessor, Level, Line,
    Material, MaterialFunctionAssignment, ShellLayerType, StairsEditScope, Transaction, UnitTypeId, UnitUtils, UV, View, Wall,
    WallKind, WallType, XYZ,
)
from Autodesk.Revit.DB.Architecture import (  # noqa: E402
    StairsLanding, StairsRun, StairsRunJustification, StairsType,
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
STRUCTURAL_FAMILIES = {
    "column": (BuiltInCategory.OST_StructuralColumns,
               ("M_Concrete-Rectangular-Column", "Concrete-Rectangular-Column")),
    "beam": (BuiltInCategory.OST_StructuralFraming,
             ("M_Concrete-Rectangular Beam", "Concrete-Rectangular Beam", "M_Concrete-Rectangular-Beam")),
}
FOOTING_FAMILIES = ("M_Footing-Rectangular", "Footing-Rectangular")


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
        self._stubs = []  # columns standing on the footings
        self.cut_info = {}  # opening id -> (placement method, wall volume removed m3, expected m3)
        self.report = {"levels": 0, "levels_renamed": [], "levels_removed": [], "wall_types": 0, "walls": 0, "doors": 0, "windows": 0,
                       "floors": 0, "columns": 0, "beams": 0, "rooms": [], "openings": [], "warnings": [],
                       "footing_moves": [], "footing_tops": [], "column_bases": [], "stairs": []}

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
            elif level.Name != spec["name"]:
                old = level.Name
                try:
                    level.Name = spec["name"]
                    self.report["levels_renamed"].append("{0} -> {1}".format(old, spec["name"]))
                except Exception as e:  # name already taken by another level
                    self.warn("Level '{0}' kept its name, could not rename to '{1}': {2}".format(old, spec["name"], e))
            self.levels[spec["id"]] = level
        self.remove_unused_levels(existing)

    def remove_unused_levels(self, levels):
        """Delete template levels the building does not use, so IFC has no empty storeys.

        A level is kept if any model element sits on it; its plan views go with it.
        """
        used = list(self.levels.values())
        for lv in levels:
            if any(lv.Id == u.Id for u in used):  # ElementId equality; IntegerValue is gone in recent Revit
                continue
            on_level = [e for e in FilteredElementCollector(self.doc).WherePasses(ElementLevelFilter(lv.Id))
                        .WhereElementIsNotElementType() if not isinstance(e, View)]
            name = lv.Name
            if on_level:
                self.warn("Level '{0}' is not in the building JSON but hosts {1} elements; kept".format(
                    name, len(on_level)))
                continue
            try:
                self.doc.Delete(lv.Id)
                self.report["levels_removed"].append(name)
            except Exception as e:  # e.g. one of its views is the active view
                self.warn("Unused level '{0}' could not be deleted ({1}); switch to another view and delete it".format(
                    name, e))

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

    @staticmethod
    def _loop(pts, z):
        loop = CurveLoop()
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            loop.Append(Line.CreateBound(XYZ(ft(a[0]), ft(a[1]), z), XYZ(ft(b[0]), ft(b[1]), z)))
        return loop

    def make_floors(self):
        for spec in self.data.get("floors", []):
            level = self.levels[spec["level"]]
            loops = List[CurveLoop]()
            loops.Add(self._loop(spec["boundary"], level.Elevation))
            for hole in spec.get("openings", []):  # inner loops cut holes (the stair well)
                loops.Add(self._loop(hole, level.Elevation))
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

    def _structural_family(self, kind):
        """(family name or None, its symbols, names of all loaded families in the category)."""
        category, preferred = STRUCTURAL_FAMILIES[kind]
        symbols = list(FilteredElementCollector(self.doc).OfCategory(category).OfClass(FamilySymbol))
        families = sorted(set(_name(s.Family) for s in symbols))
        family = next((f for f in preferred if f in families), None)
        if family is None:
            family = next((f for f in families if "concrete" in f.lower() and "rect" in f.lower()), None)
        return family, [s for s in symbols if _name(s.Family) == family], families

    def check_families(self):
        """Stop before anything is created if a family the model needs is not loaded (Dynamo commits a
        half-built model otherwise)."""
        missing = []
        for kind in ("column", "beam"):
            if self.data.get(kind + "s") and self._structural_family(kind)[0] is None:
                found = self._structural_family(kind)[2]
                missing.append("concrete rectangular {0}: load '{1}' (Structural > {2}; loaded now: {3})".format(
                    kind, STRUCTURAL_FAMILIES[kind][1][0], "Columns > Concrete" if kind == "column"
                    else "Framing > Concrete", ", ".join(found) or "none"))
        if self.data.get("footings"):
            names = set(_name(s.Family) for s in FilteredElementCollector(self.doc)
                        .OfCategory(BuiltInCategory.OST_StructuralFoundation).OfClass(FamilySymbol))
            if not any(f in FOOTING_FAMILIES or ("footing" in f.lower() and "rect" in f.lower()) for f in names):
                missing.append("rectangular footing: load '{0}' (Structural > Foundations)".format(FOOTING_FAMILIES[0]))
        if missing:
            raise RuntimeError("Nothing was built. Load these families with Insert > Load Autodesk Family, then run "
                               "again:\n  " + "\n  ".join(missing))

    def structural_type(self, kind, width, depth):
        """A concrete column/beam type of width x depth, duplicated from the loaded family."""
        family, symbols, families = self._structural_family(kind)
        if family is None:
            raise RuntimeError("No concrete rectangular {0} family loaded (found: {1}).".format(
                kind, ", ".join(families) or "none"))
        name = "STRATA {0} {1:.0f}x{2:.0f}".format("C" if kind == "column" else "B", width * 1000, depth * 1000)
        sym = next((s for s in symbols if _name(s) == name), None)
        if sym is None:
            sym = self.doc.GetElement(symbols[0].Duplicate(name).Id)
            # b = width across, h = depth (section height) in the Autodesk concrete families
            for label, value in (("b", width), ("h", depth)):
                p = sym.LookupParameter(label)
                if p is None or p.IsReadOnly:
                    self.warn("{0} type '{1}': no settable '{2}' parameter".format(kind, name, label))
                else:
                    p.Set(ft(value))
        if not sym.IsActive:
            sym.Activate()
            self.doc.Regenerate()
        return sym

    def _comment(self, element, design):
        """Write the STAAD result summary into the element's Comments."""
        if not design:
            return
        if "bearing_kn_m2" in design:  # footing: sized from STAAD reactions
            parts = ["Footing: {0} kN DL+LL, bearing {1}/{2} kN/m2 under {3}".format(
                design.get("service_kn"), design["bearing_kn_m2"], design.get("allowed_kn_m2"),
                design.get("governing"))]
        else:
            parts = ["STAAD {0}".format(design.get("status", "?"))]
        if "bars" in design:
            parts.append("{0}, {1}% steel, IR {2}".format(design["bars"], design.get("steel_ratio_pct"),
                                                          design.get("interaction_ratio")))
        if "top_steel_provided_mm2" in design:
            parts.append("top {0}/{1} mm2, bottom {2}/{3} mm2, stirrups @{4} mm".format(
                design["top_steel_required_mm2"], design["top_steel_provided_mm2"],
                design["bottom_steel_required_mm2"], design["bottom_steel_provided_mm2"],
                design["min_stirrup_spacing_mm"]))
        if design.get("code"):
            parts.append(design["code"])
        p = element.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if p is not None and not p.IsReadOnly:
            p.Set("; ".join(parts))

    def make_columns(self):
        for spec in self.data.get("columns", []):
            base, top = self.levels[spec["base_level"]], self.levels[spec["top_level"]]
            sym = self.structural_type("column", spec["width"], spec["depth"])
            point = XYZ(ft(spec["at"][0]), ft(spec["at"][1]), base.Elevation)
            col = self.doc.Create.NewFamilyInstance(point, sym, base, StructuralType.Column)
            col.get_Parameter(BuiltInParameter.FAMILY_TOP_LEVEL_PARAM).Set(top.Id)
            col.get_Parameter(BuiltInParameter.FAMILY_BASE_LEVEL_OFFSET_PARAM).Set(0.0)
            col.get_Parameter(BuiltInParameter.FAMILY_TOP_LEVEL_OFFSET_PARAM).Set(0.0)
            self._comment(col, spec.get("design"))
            self.report["columns"] += 1
            if spec["base_level"] == "LF":
                self._stubs.append(col)
        self.doc.Regenerate()
        for col in self._stubs:
            bb = col.get_BoundingBox(None)
            if bb is not None:
                self.report["column_bases"].append(UnitUtils.ConvertFromInternalUnits(bb.Min.Z, UnitTypeId.Meters))

    def make_beams(self):
        for spec in self.data.get("beams", []):
            level = self.levels[spec["level"]]
            sym = self.structural_type("beam", spec["width"], spec["depth"])
            (x1, y1), (x2, y2) = spec["start"], spec["end"]
            line = Line.CreateBound(XYZ(ft(x1), ft(y1), level.Elevation), XYZ(ft(x2), ft(y2), level.Elevation))
            beam = self.doc.Create.NewFamilyInstance(line, sym, level, StructuralType.Beam)
            self._comment(beam, spec.get("design"))
            self.report["beams"] += 1
        self.doc.Regenerate()

    def footing_type(self, width, length, thickness):
        """A rectangular pad footing type of the given size, duplicated from the loaded family."""
        symbols = list(FilteredElementCollector(self.doc).OfCategory(BuiltInCategory.OST_StructuralFoundation)
                       .OfClass(FamilySymbol))
        families = sorted(set(_name(s.Family) for s in symbols))
        family = next((f for f in FOOTING_FAMILIES if f in families), None)
        if family is None:
            family = next((f for f in families if "footing" in f.lower() and "rect" in f.lower()), None)
        if family is None:
            return None
        symbols = [s for s in symbols if _name(s.Family) == family]
        name = "STRATA F {0:.0f}x{1:.0f}x{2:.0f}".format(width * 1000, length * 1000, thickness * 1000)
        sym = next((s for s in symbols if _name(s) == name), None)
        if sym is None:
            sym = self.doc.GetElement(symbols[0].Duplicate(name).Id)
            for label, value in (("Width", width), ("Length", length), ("Foundation Thickness", thickness)):
                p = sym.LookupParameter(label)
                if p is None or p.IsReadOnly:
                    self.warn("footing type '{0}': no settable '{1}' parameter".format(name, label))
                else:
                    p.Set(ft(value))
        if not sym.IsActive:
            sym.Activate()
            self.doc.Regenerate()
        return sym

    def make_footings(self):
        for spec in self.data.get("footings", []):
            level = self.levels[spec["level"]]
            sym = self.footing_type(spec["width"], spec["length"], spec["thickness"])
            if sym is None:
                self.warn("No rectangular footing family loaded; load 'M_Footing-Rectangular' "
                          "(Insert > Load Autodesk Family > Structural Foundations) to build the footings")
                return
            point = XYZ(ft(spec["at"][0]), ft(spec["at"][1]), level.Elevation)  # top of the pad at the level
            pad = self.doc.Create.NewFamilyInstance(point, sym, level, StructuralType.Footing)
            self._comment(pad, spec.get("design"))
            self.doc.Regenerate()
            # Where Revit puts the pad depends on the family and the overload (the point's z can be read as an
            # offset from the level): measure it and move it so its top is at the level, centred on `at`
            bb = pad.get_BoundingBox(None)
            if bb is not None:
                move = XYZ(point.X - (bb.Min.X + bb.Max.X) / 2.0, point.Y - (bb.Min.Y + bb.Max.Y) / 2.0,
                           level.Elevation - bb.Max.Z)
                if move.GetLength() > ft(0.001):
                    ElementTransformUtils.MoveElement(self.doc, pad.Id, move)
                    self.report["footing_moves"].append(UnitUtils.ConvertFromInternalUnits(move.Z, UnitTypeId.Meters))
                    self.doc.Regenerate()
                bb = pad.get_BoundingBox(None)
                self.report["footing_tops"].append(UnitUtils.ConvertFromInternalUnits(bb.Max.Z, UnitTypeId.Meters))
            self.report["footings"] = self.report.get("footings", 0) + 1
        self.doc.Regenerate()

    def build(self):
        self.check_families()
        self.make_levels()
        self.make_wall_types()
        self.make_footings()
        self.make_columns()
        self.make_beams()
        self.make_walls()
        self.make_openings()
        self.check_openings()
        self.make_floors()
        self.make_rooms()
        return self.report


def _warning_swallower():
    """An IFailuresPreprocessor that accepts warnings (e.g. a riser above the stair type's maximum), so a
    warning does not roll the stair back. Defined per call with a fresh .NET namespace: Python.NET cannot
    define the same .NET type twice, and Dynamo reloads this module on every run."""
    class WarningSwallower(IFailuresPreprocessor):
        __namespace__ = "STRATA_" + uuid.uuid4().hex[:12]

        def PreprocessFailures(self, accessor):
            for f in accessor.GetFailureMessages():
                if f.GetSeverity() == FailureSeverity.Warning:
                    accessor.DeleteWarning(f)
            return FailureProcessingResult.Continue

    return WarningSwallower()


def _make_stair(builder, spec, preprocessor):
    doc = builder.doc
    base, top = builder.levels[spec["base_level"]], builder.levels[spec["top_level"]]
    scope = StairsEditScope(doc, "STRATA stair " + spec["id"])
    try:
        stairs_id = scope.Start(base.Id, top.Id)
        t = Transaction(doc, "STRATA stair " + spec["id"] + " runs")
        t.Start()
        try:
            stairs = doc.GetElement(stairs_id)
            stairs.DesiredRisersNumber = sum(r["risers"] for r in spec["runs"])
            stairs.ActualTreadDepth = ft(spec["tread"])
            # A run's path z is its base in model coordinates (>= the stair's base); a landing's
            # elevation is relative to the stair's base level. Both from the JSON, not Revit's rounding.
            z0, climbed = base.ProjectElevation, 0
            for r in spec["runs"]:
                (x0, y0), (x1, y1) = r["start"], r["end"]
                z = z0 + ft(climbed * spec["riser"])
                line = Line.CreateBound(XYZ(ft(x0), ft(y0), z), XYZ(ft(x1), ft(y1), z))
                run = StairsRun.CreateStraightRun(doc, stairs_id, line, StairsRunJustification.Center)
                run.ActualRunWidth = ft(spec["width"])
                climbed += r["risers"]
            for ld in spec.get("landings", []):
                StairsLanding.CreateSketchedLanding(
                    doc, stairs_id, Builder._loop(ld["boundary"], z0 + ft(ld["height"])), ft(ld["height"]))
            t.Commit()
        except Exception:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            raise
        scope.Commit(preprocessor)
    except Exception:
        if scope.IsActive:
            scope.Cancel()
        raise
    stairs = doc.GetElement(stairs_id)
    builder.report["stairs"].append("{0}: {1} -> {2}, {3} risers of {4:.0f} mm, tread {5:.0f} mm".format(
        spec["id"], spec["base_level"], spec["top_level"], stairs.ActualRisersNumber,
        UnitUtils.ConvertFromInternalUnits(stairs.ActualRiserHeight, UnitTypeId.Millimeters),
        UnitUtils.ConvertFromInternalUnits(stairs.ActualTreadDepth, UnitTypeId.Millimeters)))


STAIR_TYPE = "STRATA Stair"


def _stair_type(builder, specs):
    """A stair type whose riser/tread/width limits are the code limits in the JSON (NBC 2016 for a
    house), made the default so new stairs use it. Revit's stock type (max riser 180, min tread 280 mm)
    would flag every code-compliant stair."""
    doc = builder.doc
    max_riser = max(s.get("max_riser") or s["riser"] for s in specs)
    min_tread = min(s.get("min_tread") or s["tread"] for s in specs)
    min_width = min(s["width"] for s in specs)
    t = Transaction(doc, "STRATA stair type")
    t.Start()
    try:
        st = next((x for x in FilteredElementCollector(doc).OfClass(StairsType)
                   if _name(x) == STAIR_TYPE), None)
        if st is None:
            base = doc.GetElement(doc.GetDefaultElementTypeId(ElementTypeGroup.StairsType))
            st = doc.GetElement(base.Duplicate(STAIR_TYPE).Id)
        st.MaxRiserHeight = ft(max_riser)
        st.MinTreadDepth = ft(min_tread)
        st.MinRunWidth = ft(min_width)
        doc.SetDefaultElementTypeId(ElementTypeGroup.StairsType, st.Id)
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    builder.report["stairs"].append("type {0}: max riser {1:.0f} mm, min tread {2:.0f} mm, min width {3:.0f} mm"
                                    .format(STAIR_TYPE, max_riser * 1000, min_tread * 1000, min_width * 1000))


def make_stairs(builder):
    """Build the stairs after the caller has committed build()'s transaction. A failed stair is a
    warning in the report; the rest of the model stays."""
    specs = builder.data.get("stairs", [])
    if not specs:
        return builder.report
    try:
        _stair_type(builder, specs)
    except Exception as e:  # noqa: BLE001 - the stock type still builds, with Revit's warnings
        builder.warn("stair type not set: {0}".format(e))
    try:
        preprocessor = _warning_swallower()
    except Exception as e:  # noqa: BLE001 - no preprocessor: Revit's own failure handling
        builder.warn("stairs: warnings cannot be accepted automatically ({0})".format(e))
        preprocessor = None
    for spec in specs:
        try:
            _make_stair(builder, spec, preprocessor)
        except Exception as e:  # noqa: BLE001
            builder.warn("stair {0} not built: {1}".format(spec["id"], e))
    return builder.report


def load(path):
    with open(path) as f:
        return json.load(f)


def build_from_file(doc, path):
    """Everything but the stairs, inside the caller's transaction; then call make_stairs(builder)."""
    builder = Builder(doc, load(path))
    builder.build()
    return builder


def _range(values):
    if not values:
        return "none"
    lo, hi = min(values), max(values)
    return "{0:.3f}".format(lo) if abs(hi - lo) < 0.0005 else "{0:.3f} to {1:.3f}".format(lo, hi)


def format_report(report):
    lines = ["STRATA build complete",
             "Levels created: {0}".format(report["levels"]),
             "Levels renamed: {0}".format(", ".join(report.get("levels_renamed", [])) or "none"),
             "Template levels removed: {0}".format(", ".join(report.get("levels_removed", [])) or "none"),
             "Wall types created: {0}".format(report["wall_types"]),
             "Footings: {0}  Columns: {1}  Beams: {2}".format(report.get("footings", 0), report.get("columns", 0),
                                                              report.get("beams", 0)),
             "Walls: {0}  Doors: {1}  Windows: {2}  Floors: {3}".format(
                 report["walls"], report["doors"], report["windows"], report["floors"]),
             "Rooms: " + ", ".join(report["rooms"]),
             "Stairs: " + ("; ".join(report.get("stairs", [])) or "none"),
             "Footing tops (m): " + _range(report.get("footing_tops", []))
             + ("  (moved by " + _range(report["footing_moves"]) + " m to meet the columns)"
                if report.get("footing_moves") else ""),
             "Column bases on footings (m): " + _range(report.get("column_bases", [])),
             "Openings:"] + ["  " + o for o in report.get("openings", [])]
    if report["warnings"]:
        lines.append("Warnings:")
        lines += ["  - " + w for w in report["warnings"]]
    return "\n".join(lines)
