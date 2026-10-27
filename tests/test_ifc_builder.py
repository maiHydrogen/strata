"""Tests for the deterministic IFC builder, the Revit-readiness checks and the script executor."""

import os

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import pytest

from src.execution import orchestrator
from src.execution.ifc_builder import IfcBuilder
from src.execution.ifc_checks import check_ifc

PROMPT_PATH = os.path.join(os.path.dirname(orchestrator.__file__), "prompts", "coder_ifc_prompt.txt")


def _flat(path):
    """2-room flat, 8 x 6 m, one storey."""
    b = IfcBuilder(project_name="Flat")
    gf = b.add_storey("Ground Floor", 0.0, 3.0)
    outer = [(0, 0), (8, 0), (8, 6), (0, 6)]
    ext = b.add_walls_polygon(gf, outer, thickness=0.23)
    part = b.add_wall(gf, (4, 0), (4, 6), thickness=0.115, name="Partition")
    b.add_door(ext[0], 1.5, name="Main Door")
    b.add_door(part, 2.5, name="Inner Door")
    b.add_window(ext[2], 5.3)
    b.add_slab(gf, outer)
    b.add_space(gf, [(0, 0), (4, 0), (4, 6), (0, 6)], "Living Room")
    b.add_space(gf, [(4, 0), (8, 0), (8, 6), (4, 6)], "Bedroom")
    return b, b.save(path)


def _house(path):
    """Two-storey 10 x 8 m house with a roof."""
    b = IfcBuilder(project_name="House")
    outer = [(0, 0), (10, 0), (10, 8), (0, 8)]
    for i, name in enumerate(["Ground Floor", "First Floor"]):
        st = b.add_storey(name, i * 3.0, 3.0)
        ext = b.add_walls_polygon(st, outer, thickness=0.23)
        hall = b.add_wall(st, (5, 0), (5, 8), thickness=0.115)
        b.add_door(ext[0], 4.0, name=f"Door {i}")
        b.add_door(hall, 3.0)
        b.add_window(ext[2], 1.0)
        b.add_slab(st, outer)
        b.add_space(st, [(0, 0), (5, 0), (5, 8), (0, 8)], f"Room A{i}")
        b.add_space(st, [(5, 0), (10, 0), (10, 8), (5, 8)], f"Room B{i}")
    b.add_slab(st, outer, predefined="ROOF", top=3.0)
    return b, b.save(path)


def test_flat_passes_checks(tmp_path):
    _, path = _flat(tmp_path / "flat.ifc")
    result = check_ifc(path)
    assert result["errors"] == []
    assert result["counts"]["IfcWall"] == 5
    assert result["counts"]["IfcDoor"] == 2
    assert result["counts"]["IfcSpace"] == 2


def test_house_has_two_storeys_at_correct_elevations(tmp_path):
    _, path = _house(tmp_path / "house.ifc")
    assert check_ifc(path)["errors"] == []
    model = ifcopenshell.open(path)
    assert sorted(s.Elevation for s in model.by_type("IfcBuildingStorey")) == [0.0, 3.0]
    assert len(model.by_type("IfcSlab")) == 3


def test_placement_chain_and_units(tmp_path):
    _, path = _flat(tmp_path / "flat.ifc")
    model = ifcopenshell.open(path)
    assert model.schema == "IFC4"
    assert model.by_type("IfcSIUnit")[0].Name == "METRE" and model.by_type("IfcSIUnit")[0].Prefix is None
    for cls in ("IfcSite", "IfcBuilding", "IfcBuildingStorey"):
        assert model.by_type(cls)[0].ObjectPlacement is not None
    wall = model.by_type("IfcWall")[0]
    assert wall.ObjectPlacement.PlacementRelTo == model.by_type("IfcBuildingStorey")[0].ObjectPlacement


def test_door_sits_inside_its_wall(tmp_path):
    _, path = _flat(tmp_path / "flat.ifc")
    model = ifcopenshell.open(path)
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    door = next(d for d in model.by_type("IfcDoor") if d.Name == "Main Door")
    xs, ys, zs = zip(*[tuple(v) for v in _verts(ifcopenshell.geom.create_shape(settings, door))])
    assert min(xs) == pytest.approx(1.5) and max(xs) == pytest.approx(2.4)
    assert min(ys) == pytest.approx(-0.025) and max(ys) == pytest.approx(0.025)
    assert min(zs) == pytest.approx(0.0) and max(zs) == pytest.approx(2.1)


def _verts(shape):
    v = shape.geometry.verts
    return [(v[i], v[i + 1], v[i + 2]) for i in range(0, len(v), 3)]


def test_materials_and_psets(tmp_path):
    _, path = _flat(tmp_path / "flat.ifc")
    model = ifcopenshell.open(path)
    wall = model.by_type("IfcWall")[0]
    assert ifcopenshell.util.element.get_material(wall) is not None
    assert ifcopenshell.util.element.get_psets(wall)["Pset_WallCommon"]["IsExternal"] is True


def test_checks_flag_door_without_geometry(tmp_path):
    _, path = _flat(tmp_path / "flat.ifc")
    model = ifcopenshell.open(path)
    model.by_type("IfcDoor")[0].Representation = None
    model.write(str(path))
    errors = check_ifc(path)["errors"]
    assert any("has no geometry" in e for e in errors)


def test_opening_too_wide_raises_and_misplaced_is_clamped():
    b = IfcBuilder()
    st = b.add_storey("GF")
    wall = b.add_wall(st, (0, 0), (2, 0))
    with pytest.raises(ValueError):
        b.add_door(wall, 0.0, width=3.0)
    b.add_door(wall, 1.5, width=0.9)
    assert any("moved to 1.100" in w for w in b.warnings)


def test_degenerate_inputs_rejected():
    b = IfcBuilder()
    st = b.add_storey("GF")
    with pytest.raises(ValueError):
        b.add_wall(st, (1, 1), (1, 1))
    with pytest.raises(ValueError):
        b.add_slab(st, [(0, 0), (1, 0)])


@pytest.fixture
def isolated_output(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "OUTPUT_DIR", str(tmp_path))
    return tmp_path


def _prompt_example():
    return open(PROMPT_PATH, encoding="utf-8").read().split('"""')[1]


def test_prompt_example_script_executes_and_validates(isolated_output):
    path, log, ok = orchestrator.execute_ifc_script(_prompt_example())
    assert ok, log
    assert path == str(isolated_output / "building.ifc")
    assert orchestrator.validate_ifc_file(path)["errors"] == []


def test_executor_blocks_disallowed_imports_and_reports_error(isolated_output):
    path, log, ok = orchestrator.execute_ifc_script("import os\nos.remove('x')")
    assert not ok and path is None and "not allowed" in log


def test_executor_ignores_stale_file_and_saves_unsaved_model(isolated_output):
    (isolated_output / "building.ifc").write_text("stale")
    path, log, ok = orchestrator.execute_ifc_script("x = 1")
    assert not ok and not (isolated_output / "building.ifc").exists()

    code = "b = IfcBuilder()\nst = b.add_storey('GF')\nb.add_wall(st, (0, 0), (3, 0))"
    path, log, ok = orchestrator.execute_ifc_script(code)
    assert ok and os.path.exists(path)
