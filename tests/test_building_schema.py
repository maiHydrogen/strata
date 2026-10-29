"""Tests for the building JSON schema, its cross-checks, and the Revit builder's source."""

import ast
import json
import os

import pytest
from pydantic import ValidationError

from src.execution.building_schema import Building, validate_building

ROOT = os.path.dirname(os.path.dirname(__file__))
SAMPLE = os.path.join(ROOT, "examples", "two_room_flat.building.json")
BUILDER = os.path.join(ROOT, "revit", "STRATA.extension", "lib", "strata_builder.py")


def _sample():
    with open(SAMPLE) as f:
        return json.load(f)


def test_sample_is_valid():
    b = Building.model_validate(_sample())
    assert validate_building(b) == []
    assert len(b.walls) == 5 and len(b.openings) == 4 and len(b.rooms) == 2
    assert b.wall_types[0].thickness == pytest.approx(0.257)


def test_unknown_references_are_reported():
    data = _sample()
    data["walls"][0]["type"] = "NOPE"
    data["openings"][0]["wall"] = "W99"
    data["rooms"][0]["level"] = "L9"
    problems = validate_building(Building.model_validate(data))
    assert any("unknown wall type 'NOPE'" in p for p in problems)
    assert any("unknown wall 'W99'" in p for p in problems)
    assert any("unknown level 'L9'" in p for p in problems)


def test_opening_must_fit_its_wall():
    data = _sample()
    data["openings"][0]["offset"] = 7.5  # 7.5 + 1.0 > 8 m wall
    data["openings"][2]["height"] = 2.5  # sill 0.9 + 2.5 > 3.0 m wall
    problems = validate_building(Building.model_validate(data))
    assert any("D1" in p and "exceeds wall" in p for p in problems)
    assert any("WIN1" in p and "exceeds wall height" in p for p in problems)


def test_floor_and_room_geometry_checks():
    data = _sample()
    data["floors"][0]["boundary"] = [[0, 0], [8, 0], [16, 0]]
    data["rooms"][1]["point"] = [20, 20]
    problems = validate_building(Building.model_validate(data))
    assert any("no area" in p for p in problems)
    assert any("R2" in p and "not inside" in p for p in problems)


def test_duplicate_ids_and_bad_values():
    data = _sample()
    data["walls"][1]["id"] = "W1"
    assert any("duplicate wall id 'W1'" in p for p in validate_building(Building.model_validate(data)))
    data = _sample()
    data["openings"][0]["width"] = 0
    with pytest.raises(ValidationError):
        Building.model_validate(data)


def test_builder_source_runs_on_ironpython_and_cpython():
    """The builder must parse as Python 3 and avoid f-strings (pyRevit's default engine is IronPython 2.7)."""
    tree = ast.parse(open(BUILDER, encoding="utf-8").read())
    assert not any(isinstance(n, ast.JoinedStr) for n in ast.walk(tree))
    assert not any(isinstance(n, ast.AnnAssign) for n in ast.walk(tree))
