"""Post-generation checks that an IFC file is likely to open cleanly in Revit."""

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.validate

GEOMETRIC_CLASSES = ("IfcWall", "IfcSlab", "IfcDoor", "IfcWindow", "IfcSpace", "IfcOpeningElement")
MAX_EXTENT_M = 1000.0


def check_ifc(path):
    """Return {"errors": [...], "warnings": [...], "counts": {...}} for the IFC at `path`."""
    errors, warnings = [], []
    model = ifcopenshell.open(str(path))

    if model.schema != "IFC4":
        warnings.append(f"Schema is {model.schema}, expected IFC4")

    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(model, logger)
    errors += [f"Schema validation: {st['message']}" for st in logger.statements[:10]]

    counts = {cls: len(model.by_type(cls)) for cls in GEOMETRIC_CLASSES + ("IfcBuildingStorey",)}
    if not counts["IfcBuildingStorey"]:
        errors.append("No IfcBuildingStorey in model")
    if not counts["IfcWall"]:
        errors.append("No IfcWall in model")

    for spatial in model.by_type("IfcSpatialStructureElement"):
        if spatial.ObjectPlacement is None and not spatial.is_a("IfcSpace"):
            errors.append(f"{spatial.is_a()} '{spatial.Name}' has no ObjectPlacement")

    for cls in GEOMETRIC_CLASSES:
        for el in model.by_type(cls):
            label = f"{cls} '{el.Name}' (#{el.id()})"
            if el.Representation is None:
                errors.append(f"{label} has no geometry")
            if el.ObjectPlacement is None:
                errors.append(f"{label} has no ObjectPlacement")
            if cls in ("IfcWall", "IfcSlab", "IfcDoor", "IfcWindow") and not el.ContainedInStructure:
                errors.append(f"{label} is not contained in a storey")

    for cls in ("IfcDoor", "IfcWindow"):
        for el in model.by_type(cls):
            if not el.FillsVoids or not el.FillsVoids[0].RelatingOpeningElement.VoidsElements:
                errors.append(f"{cls} '{el.Name}' (#{el.id()}) is not hosted in a wall opening")

    settings = ifcopenshell.geom.settings()
    for el in model.by_type("IfcProduct"):
        if el.Representation is None or el.is_a("IfcOpeningElement"):
            continue
        try:
            shape = ifcopenshell.geom.create_shape(settings, el)
        except Exception as exc:
            errors.append(f"{el.is_a()} '{el.Name}' (#{el.id()}) geometry failed: {exc}")
            continue
        verts = shape.geometry.verts
        if not verts:
            errors.append(f"{el.is_a()} '{el.Name}' (#{el.id()}) produced empty geometry")
        elif max(abs(v) for v in verts) > MAX_EXTENT_M:
            warnings.append(f"{el.is_a()} '{el.Name}' (#{el.id()}) is larger than {MAX_EXTENT_M:.0f} m")

    return {"errors": errors, "warnings": warnings, "counts": counts}
