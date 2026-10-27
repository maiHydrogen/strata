"""Deterministic IFC4 model builder.

Thin layer over IfcOpenShell that always produces the structure Revit's IFC
importer expects: a full placement chain (Site -> Building -> Storey -> element),
explicit extruded-solid geometry on every element, doors/windows hosted in
wall openings, materials, and IfcSpace rooms.

All lengths are in METRES (the model's length unit is metre).
"""

import math

import ifcopenshell
import ifcopenshell.guid
from ifcopenshell.api import run

_TOL = 1e-6


def _ccw(points):
    """Return the polygon without a repeated closing point, wound counter-clockwise."""
    pts = [(float(x), float(y)) for x, y in points]
    if len(pts) > 1 and math.dist(pts[0], pts[-1]) < _TOL:
        pts = pts[:-1]
    if len(pts) < 3:
        raise ValueError("A polygon needs at least 3 distinct points")
    area2 = sum(
        pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
        for i in range(len(pts))
    )
    if abs(area2) < _TOL:
        raise ValueError("Polygon has zero area")
    return pts if area2 > 0 else pts[::-1]


class IfcBuilder:
    def __init__(self, project_name="STRATA Model", site_name="Site", building_name="Building"):
        self.model = ifcopenshell.file(schema="IFC4")
        self.warnings = []
        self._walls = {}  # wall entity id -> geometry info
        self._storeys = {}  # storey entity id -> {"placement", "height"}
        self._layer_sets = {}  # (material name, thickness) -> IfcMaterialLayerSet
        self._materials = {}

        m = self.model
        self.project = run("root.create_entity", m, ifc_class="IfcProject", name=project_name)
        run("unit.assign_unit", m, length={"is_metric": True, "raw": "METERS"})
        context = run("context.add_context", m, context_type="Model")
        self.body = run(
            "context.add_context", m, context_type="Model", context_identifier="Body",
            target_view="MODEL_VIEW", parent=context,
        )

        self.site = run("root.create_entity", m, ifc_class="IfcSite", name=site_name)
        self.building = run("root.create_entity", m, ifc_class="IfcBuilding", name=building_name)
        self.site.ObjectPlacement = self._placement()
        self.building.ObjectPlacement = self._placement(rel_to=self.site.ObjectPlacement)
        run("aggregate.assign_object", m, relating_object=self.project, products=[self.site])
        run("aggregate.assign_object", m, relating_object=self.site, products=[self.building])

    # ---- low-level geometry -------------------------------------------------

    def _placement(self, origin=(0.0, 0.0, 0.0), x_axis=(1.0, 0.0, 0.0), rel_to=None):
        m = self.model
        axis = m.createIfcAxis2Placement3D(
            m.createIfcCartesianPoint(tuple(float(v) for v in origin)),
            m.createIfcDirection((0.0, 0.0, 1.0)),
            m.createIfcDirection(tuple(float(v) for v in x_axis)),
        )
        return m.createIfcLocalPlacement(rel_to, axis)

    def _rect_profile(self, center_x, center_y, x_dim, y_dim):
        m = self.model
        pos = m.createIfcAxis2Placement2D(m.createIfcCartesianPoint((float(center_x), float(center_y))))
        return m.createIfcRectangleProfileDef("AREA", None, pos, float(x_dim), float(y_dim))

    def _polygon_profile(self, points):
        m = self.model
        pts = _ccw(points)
        ring = [m.createIfcCartesianPoint(p) for p in pts + [pts[0]]]
        return m.createIfcArbitraryClosedProfileDef("AREA", None, m.createIfcPolyline(ring))

    def _extrusion(self, profile, depth):
        m = self.model
        solid = m.createIfcExtrudedAreaSolid(
            profile,
            m.createIfcAxis2Placement3D(m.createIfcCartesianPoint((0.0, 0.0, 0.0))),
            m.createIfcDirection((0.0, 0.0, 1.0)),
            float(depth),
        )
        rep = m.createIfcShapeRepresentation(self.body, "Body", "SweptSolid", [solid])
        return m.createIfcProductDefinitionShape(None, None, [rep])

    def _material_layer_set(self, name, thickness, direction, offset):
        """Associate-ready IfcMaterialLayerSetUsage for a single-material layer."""
        m = self.model
        key = (name, round(thickness, 6))
        if key not in self._layer_sets:
            if name not in self._materials:
                self._materials[name] = run("material.add_material", m, name=name)
            layer = m.createIfcMaterialLayer(self._materials[name], float(thickness), None)
            self._layer_sets[key] = m.createIfcMaterialLayerSet([layer], f"{name} {thickness * 1000:.0f}mm")
        return m.createIfcMaterialLayerSetUsage(
            self._layer_sets[key], direction, "POSITIVE", float(offset)
        )

    def _associate_material(self, product, material):
        self.model.createIfcRelAssociatesMaterial(
            ifcopenshell.guid.new(), None, None, None, [product], material
        )

    def _pset(self, product, name, properties):
        pset = run("pset.add_pset", self.model, product=product, name=name)
        run("pset.edit_pset", self.model, pset=pset, properties=properties)

    # ---- spatial structure --------------------------------------------------

    def add_storey(self, name, elevation=0.0, height=3.0):
        storey = run("root.create_entity", self.model, ifc_class="IfcBuildingStorey", name=name)
        storey.Elevation = float(elevation)
        storey.ObjectPlacement = self._placement(
            origin=(0.0, 0.0, elevation), rel_to=self.building.ObjectPlacement
        )
        run("aggregate.assign_object", self.model, relating_object=self.building, products=[storey])
        self._storeys[storey.id()] = {"height": float(height)}
        return storey

    def _place_in_storey(self, storey, product, placement, shape):
        product.ObjectPlacement = placement
        product.Representation = shape
        run("spatial.assign_container", self.model, relating_structure=storey, products=[product])

    # ---- elements -----------------------------------------------------------

    def add_wall(self, storey, p1, p2, height=None, thickness=0.2, name=None,
                 external=False, extend_start=0.0, extend_end=0.0, material="Brick Masonry"):
        """Straight wall on the centre line p1 -> p2.

        extend_start/extend_end lengthen the solid beyond the centre-line ends
        (used to close corners). Door/window offsets stay measured from p1.
        """
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        length = math.hypot(dx, dy)
        if length < _TOL:
            raise ValueError(f"Wall {p1}->{p2} has zero length")
        if height is None:
            height = self._storeys[storey.id()]["height"]

        wall = run("root.create_entity", self.model, ifc_class="IfcWall", name=name or "Wall")
        wall.PredefinedType = "STANDARD"
        solid_len = length + extend_start + extend_end
        profile = self._rect_profile((length + extend_end - extend_start) / 2.0, 0.0, solid_len, thickness)
        placement = self._placement(
            origin=(p1[0], p1[1], 0.0), x_axis=(dx / length, dy / length, 0.0),
            rel_to=storey.ObjectPlacement,
        )
        self._place_in_storey(storey, wall, placement, self._extrusion(profile, height))
        self._associate_material(
            wall, self._material_layer_set(material, thickness, "AXIS2", -thickness / 2.0)
        )
        self._pset(wall, "Pset_WallCommon", {"IsExternal": bool(external), "LoadBearing": bool(external)})
        self._walls[wall.id()] = {
            "length": length, "thickness": float(thickness), "height": float(height),
            "storey": storey, "external": bool(external),
        }
        return wall

    def add_walls_polygon(self, storey, points, height=None, thickness=0.2, name_prefix="Wall",
                          external=True, closed=True):
        """Walls along a polyline (centre line); closed polygons get filled corners."""
        pts = [(float(x), float(y)) for x, y in points]
        if closed and math.dist(pts[0], pts[-1]) < _TOL:
            pts = pts[:-1]
        count = len(pts) if closed else len(pts) - 1
        walls = []
        for i in range(count):
            p1, p2 = pts[i], pts[(i + 1) % len(pts)]
            ext_s = thickness / 2.0 if (closed or i > 0) else 0.0
            ext_e = thickness / 2.0 if (closed or i < count - 1) else 0.0
            walls.append(self.add_wall(
                storey, p1, p2, height=height, thickness=thickness,
                name=f"{name_prefix} {i + 1}", external=external,
                extend_start=ext_s, extend_end=ext_e,
            ))
        return walls

    def add_door(self, wall, offset, width=0.9, height=2.1, sill=0.0, name="Door"):
        return self._add_opening(wall, offset, width, height, sill, name, "IfcDoor")

    def add_window(self, wall, offset, width=1.2, height=1.2, sill=0.9, name="Window"):
        return self._add_opening(wall, offset, width, height, sill, name, "IfcWindow")

    def _add_opening(self, wall, offset, width, height, sill, name, ifc_class):
        """Cut an opening in `wall` starting `offset` m from the wall's p1 and fill it."""
        info = self._walls[wall.id()]
        if width > info["length"] + _TOL:
            raise ValueError(f"{name} ({width} m) is wider than its host wall ({info['length']:.3f} m)")
        if offset < 0 or offset + width > info["length"] + _TOL:
            fixed = min(max(offset, 0.0), info["length"] - width)
            self.warnings.append(f"{name}: offset {offset:.3f} m moved to {fixed:.3f} m to fit its wall")
            offset = fixed
        if sill + height > info["height"] + _TOL:
            fixed_h = info["height"] - sill
            self.warnings.append(f"{name}: height {height:.3f} m reduced to {fixed_h:.3f} m to fit its wall")
            height = fixed_h

        m = self.model
        thickness = info["thickness"]
        local = self._placement(origin=(offset, 0.0, sill), rel_to=wall.ObjectPlacement)

        opening = run("root.create_entity", m, ifc_class="IfcOpeningElement", name=f"Opening for {name}")
        opening.PredefinedType = "OPENING"
        opening.ObjectPlacement = local
        opening.Representation = self._extrusion(
            self._rect_profile(width / 2.0, 0.0, width, thickness), height
        )
        m.createIfcRelVoidsElement(ifcopenshell.guid.new(), None, None, None, wall, opening)

        filling = run("root.create_entity", m, ifc_class=ifc_class, name=name)
        panel = 0.05 if ifc_class == "IfcDoor" else 0.04
        filling.ObjectPlacement = self._placement(origin=(offset, 0.0, sill), rel_to=wall.ObjectPlacement)
        filling.Representation = self._extrusion(
            self._rect_profile(width / 2.0, 0.0, width, panel), height
        )
        filling.OverallHeight = float(height)
        filling.OverallWidth = float(width)
        filling.PredefinedType = "DOOR" if ifc_class == "IfcDoor" else "WINDOW"
        run("spatial.assign_container", m, relating_structure=info["storey"], products=[filling])
        m.createIfcRelFillsElement(ifcopenshell.guid.new(), None, None, None, opening, filling)
        self._pset(
            filling, "Pset_DoorCommon" if ifc_class == "IfcDoor" else "Pset_WindowCommon",
            {"IsExternal": info["external"]},
        )
        return filling

    def add_slab(self, storey, polygon, thickness=0.2, predefined="FLOOR", top=0.0,
                 name=None, material="Concrete"):
        """Slab over `polygon`; its top face sits `top` m above the storey elevation."""
        slab = run("root.create_entity", self.model, ifc_class="IfcSlab", name=name or f"{predefined.title()} Slab")
        slab.PredefinedType = predefined
        placement = self._placement(origin=(0.0, 0.0, top - thickness), rel_to=storey.ObjectPlacement)
        self._place_in_storey(
            storey, slab, placement, self._extrusion(self._polygon_profile(polygon), thickness)
        )
        self._associate_material(slab, self._material_layer_set(material, thickness, "AXIS3", 0.0))
        self._pset(slab, "Pset_SlabCommon", {"IsExternal": predefined == "ROOF"})
        return slab

    def add_space(self, storey, polygon, name, height=None):
        """Room volume (IfcSpace) over `polygon`, from the storey floor up to `height`."""
        if height is None:
            height = self._storeys[storey.id()]["height"]
        space = run("root.create_entity", self.model, ifc_class="IfcSpace", name=name)
        space.LongName = name
        space.PredefinedType = "INTERNAL"
        space.ObjectPlacement = self._placement(rel_to=storey.ObjectPlacement)
        space.Representation = self._extrusion(self._polygon_profile(polygon), height)
        run("aggregate.assign_object", self.model, relating_object=storey, products=[space])
        return space

    # ---- output -------------------------------------------------------------

    def save(self, path):
        self.model.write(str(path))
        return str(path)
