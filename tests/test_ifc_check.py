"""IFC check against the building JSON, on a small IFC4 file made with IfcOpenShell."""

import ifcopenshell
import ifcopenshell.api as api

from src.execution.ifc_check import check_ifc


def _model(tmp_path, host_door=True, orphan_beam=False):
    m = api.run("project.create_file", version="IFC4")
    project = api.run("root.create_entity", m, ifc_class="IfcProject", name="STRATA")
    api.run("unit.assign_unit", m)
    site = api.run("root.create_entity", m, ifc_class="IfcSite")
    bldg = api.run("root.create_entity", m, ifc_class="IfcBuilding")
    storey = api.run("root.create_entity", m, ifc_class="IfcBuildingStorey", name="Ground")
    storey.Elevation = 0.0
    api.run("aggregate.assign_object", m, relating_object=project, products=[site])
    api.run("aggregate.assign_object", m, relating_object=site, products=[bldg])
    api.run("aggregate.assign_object", m, relating_object=bldg, products=[storey])
    wall = api.run("root.create_entity", m, ifc_class="IfcWall", name="W1")
    col = api.run("root.create_entity", m, ifc_class="IfcColumn", name="C1")
    door = api.run("root.create_entity", m, ifc_class="IfcDoor", name="D1")
    beam = api.run("root.create_entity", m, ifc_class="IfcBeam", name="B1")
    contained = [wall, col, door] + ([] if orphan_beam else [beam])
    api.run("spatial.assign_container", m, relating_structure=storey, products=contained)
    if host_door:
        opening = api.run("root.create_entity", m, ifc_class="IfcOpeningElement")
        api.run("feature.add_feature", m, feature=opening, element=wall)
        api.run("feature.add_filling", m, opening=opening, element=door)
    path = tmp_path / "x.ifc"
    m.write(str(path))
    return str(path)


BUILDING = {"levels": [{"id": "L0"}], "columns": [{}], "beams": [{}], "walls": [{}],
            "openings": [{"kind": "door"}], "footings": [], "stairs": [], "rooms": [], "floors": []}


def _failed(rep):
    return {c.name for c in rep.checks if not c.passed}


def test_matching_ifc_passes(tmp_path):
    rep = check_ifc(_model(tmp_path), BUILDING)
    assert rep.schema == "IFC4" and rep.passed, rep.lines()


def test_count_mismatch_unhosted_door_and_orphan_fail(tmp_path):
    rep = check_ifc(_model(tmp_path, host_door=False, orphan_beam=True), BUILDING | {"walls": [{}, {}]})
    assert _failed(rep) == {"IfcWall count", "doors and windows fill a wall opening", "every element sits in a storey"}
    assert any("orphans: ['IfcBeam B1']" in c.detail for c in rep.checks)


def test_services_found_by_name_and_proxies_flagged(tmp_path):
    m = ifcopenshell.open(_model(tmp_path))
    storey = m.by_type("IfcBuildingStorey")[0]
    wc = api.run("root.create_entity", m, ifc_class="IfcSanitaryTerminal", name="STRATA wc:1676527")
    lamp = api.run("root.create_entity", m, ifc_class="IfcBuildingElementProxy", name="STRATA light L0-LIGHT2:99")
    api.run("spatial.assign_container", m, relating_structure=storey, products=[wc, lamp])
    path = tmp_path / "s.ifc"
    m.write(str(path))
    b = BUILDING | {"fixtures": [{"id": "L0-WC1", "kind": "wc", "system": "plumbing"},
                                 {"id": "L0-LIGHT2", "kind": "light", "system": "lighting"},
                                 {"id": "L0-SOCKET_6A3", "kind": "socket_6a", "system": "electrical"}]}
    rep = check_ifc(str(path), b)
    by = {c.name: c for c in rep.checks}
    assert by["plumbing fixtures in the IFC"].passed and by["lighting fixtures in the IFC"].passed
    assert not by["electrical fixtures in the IFC"].passed and "socket_6a" in by["electrical fixtures in the IFC"].detail
    assert "1 typed, 1 proxies" in by["services exported as typed elements, not proxies"].detail


# ---------------------------------------------------------------- Revit vs STAAD member check (helpers)

def test_member_size_check_reads_revit_dimensions_in_mm():
    from src.execution.ifc_check import _size_problem
    col = {"width": 0.45, "depth": 0.35}
    assert _size_problem("columns", col, {"b": 350.0, "h": 450.0}, 0.001) is None  # orientation free
    assert "STAAD" in _size_problem("columns", col, {"b": 400.0, "h": 450.0}, 0.001)
    beam = {"width": 0.23, "depth": 0.45}
    assert _size_problem("beams", beam, {"b": 230.0, "h": 449.9999}, 0.001) is None
    assert _size_problem("beams", beam, {"b": 450.0, "h": 230.0}, 0.001)  # beams are not orientation free
    pad = {"width": 1.1, "length": 1.1, "thickness": 0.3}
    assert _size_problem("footings", pad, {"Width": 1100, "Length": 1100, "Foundation Thickness": 300}, 0.001) is None
    assert _size_problem("footings", pad, {"Width": 1100}, 0.001) == "size not in the IFC"


def test_member_position_check_against_the_staad_frame():
    from src.execution.ifc_check import _position_problem
    elev = {"LF": -1.5, "L0": 0.0, "L1": 3.0}
    col = {"at": [1.915, 3.715], "base_level": "LF", "top_level": "L0"}
    assert _position_problem("columns", col, None, ((1.69, 3.49, -1.5), (2.14, 3.94, 0.0)), elev, 0.001) is None
    assert "centre" in _position_problem("columns", col, None, ((1.72, 3.49, -1.5), (2.17, 3.94, 0.0)), elev, 0.001)
    assert "z" in _position_problem("columns", col, None, ((1.69, 3.49, -1.0), (2.14, 3.94, 0.0)), elev, 0.001)
    beam = {"start": [1.915, 3.715], "end": [4.87, 3.715], "level": "L0"}
    # Revit trims the beam at the column faces: inside the span is fine
    assert _position_problem("beams", beam, None, ((2.14, 3.6, -0.45), (4.645, 3.83, 0.0)), elev, 0.001) is None
    assert _position_problem("beams", beam, None, ((2.14, 3.65, -0.45), (4.645, 3.88, 0.0)), elev, 0.001)
    assert "top" in _position_problem("beams", beam, None, ((2.14, 3.6, 2.55), (4.645, 3.83, 3.0)), elev, 0.001)
    slope = {"start": [0, 0], "end": [3, 4], "level": "L0"}
    assert _position_problem("beams", slope, None, ((1.4, 1.9, -0.45), (1.6, 2.1, 0.0)), elev, 0.001) is None
    assert "off the STAAD line" in _position_problem("beams", slope, None, ((1.9, 1.9, -0.45), (2.1, 2.1, 0.0)), elev, 0.001)
