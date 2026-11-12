"""Plan library: ResPlan conversion on a synthetic plan in ResPlan's format, retrieval, floor pairing."""

import io
import pickle

import pytest
from shapely.geometry import box
from shapely.ops import unary_union

from src.spatial.library import _AllowList, convert, features, floor_compatibility, retrieve

K = 0.1  # metres per ResPlan unit in the synthetic plan


def synthetic_plan(front_door_side="bottom"):
    """10 x 8 m house, 0.4 m walls: living (front left), bedroom (front right), kitchen open to the
    living room (rear left), bathroom off the bedroom (rear right)."""
    living, bedroom = box(4, 4, 56, 46), box(60, 4, 96, 46)
    kitchen, bath = box(4, 46, 56, 76), box(60, 50, 96, 76)
    inner = box(4, 4, 96, 76)
    front = {"bottom": box(20, 0, 31, 4), "right": box(96, 60, 100, 71)}[front_door_side]
    if front_door_side == "right":  # the entrance then opens into the bathroom side; fine for orientation tests
        front = box(96, 20, 100, 31)
    return {"id": 7, "net_area": inner.area * K * K, "inner": inner, "wall_depth": 4.0,
            "wall": box(0, 0, 100, 80).difference(unary_union([living, bedroom, kitchen, bath])),
            "living": living, "bedroom": bedroom, "kitchen": kitchen, "bathroom": bath,
            "door": unary_union([box(56, 20, 60, 29), box(70, 46, 78, 50)]),
            "front_door": front, "window": unary_union([box(70, 0, 85, 4), box(0, 60, 4, 72)])}


def test_convert_scales_grows_and_connects_rooms():
    layout, quality = convert(synthetic_plan(), "train")
    f = layout.floors[0]
    rooms = {r.type: r for r in f.rooms}
    assert set(rooms) == {"living", "bedroom", "kitchen", "bath_wc"}
    # living 5.2 x 4.2 inside its walls -> to the outer face at the front and left, to the centreline of the
    # wall with the bedroom, and not into the open-plan kitchen: 5.8 x 4.6
    assert rooms["living"].shape.area == pytest.approx(5.8 * 4.6, rel=0.02)
    pairs = {frozenset(d.between): d for d in f.doors}
    assert pairs[frozenset(("living1", "bedroom1"))].width == pytest.approx(0.9, abs=0.01)
    assert pairs[frozenset(("bedroom1", "bath_wc1"))].kind == "door"
    assert pairs[frozenset(("living1", "kitchen1"))].kind == "opening"  # open plan, no wall
    main = next(d for d in f.doors if d.main)
    assert main.between == ("living1", "outside") and main.at[1] == pytest.approx(0.0, abs=0.01)
    assert {w.room for w in f.windows} == {"bedroom1", "kitchen1"}
    assert layout.source["licence"] == "CC BY 4.0" and quality["footprint_m2"] == pytest.approx(80.0, rel=0.02)
    row = features(layout, quality)
    assert (row["width"], row["depth"]) == (pytest.approx(10.0, abs=0.05), pytest.approx(8.0, abs=0.05))
    assert row["bedrooms"] == 1 and row["bathrooms"] == 1 and row["door_pairs"]["living-outside"] == 1


def test_entrance_is_rotated_to_the_front():
    layout, _ = convert(synthetic_plan("right"), None)
    main = next(d for d in layout.floors[0].doors if d.main)
    assert main.at[1] == pytest.approx(0.0, abs=0.01) and layout.source["rotated_deg"] == 90
    row = features(layout, {"footprint_m2": 80.0})
    assert (row["width"], row["depth"]) == (pytest.approx(8.0, abs=0.05), pytest.approx(10.0, abs=0.05))


def test_plan_without_scale_is_dropped():
    plan = synthetic_plan()
    plan["net_area"] = 0.0
    layout, reason = convert(plan)
    assert layout is None and "scale" in reason


def _row(pid, w, d, beds, baths, stair=None, xs=(), ys=(), split="train"):
    return {"id": pid, "split": split, "width": w, "depth": d, "bedrooms": beds, "bathrooms": baths,
            "stair": stair, "lines_x": list(xs), "lines_y": list(ys)}


def test_retrieve_fits_the_envelope_and_ranks_by_programme():
    index = [_row(1, 9.0, 12.0, 3, 2), _row(2, 8.0, 11.0, 2, 2), _row(3, 12.0, 12.0, 3, 2),
             _row(4, 8.5, 12.5, 3, 2, split="augmented"), _row(5, 8.0, 12.0, 3, 1)]
    got = [p["id"] for p in retrieve(index, width=8.6, depth=12.9, bedrooms=3, bathrooms=2, k=3)]
    assert got == [5, 2]  # 1 and 3 are too wide, 4 is an augmented copy; 5 matches bedrooms, 2 is one off
    assert retrieve(index, 8.6, 12.9, 3, 2, stair=True) == []


def test_floor_pairing_needs_same_stair_fitting_footprint_and_wall_lines():
    ground = _row(1, 8.0, 11.0, 1, 1, stair=[0, 4, 3.6, 7.6], xs=[0, 3.6, 4.8, 8.0], ys=[0, 4.0, 7.6, 11.0])
    upper = _row(2, 8.0, 7.6, 2, 1, stair=[0, 4, 3.6, 7.6], xs=[0, 3.6, 4.8, 8.0], ys=[0, 4.0, 7.6])
    assert floor_compatibility(ground, upper)["ok"]
    moved = dict(upper, stair=[4.4, 4, 8.0, 7.6])
    assert not floor_compatibility(ground, moved)["ok"]
    off_grid = dict(upper, lines_x=[0, 2.0, 5.5, 8.0])  # walls that would stand on the slab below
    r = floor_compatibility(ground, off_grid)
    assert not r["ok"] and r["lines_on_lower"] == 0.5
    assert not floor_compatibility(ground, dict(upper, stair=None))["ok"]


def test_allow_list_unpickler_refuses_other_callables():
    payload = pickle.dumps(eval)  # a pickle that would hand back a builtin
    with pytest.raises(pickle.UnpicklingError, match="not allowed"):
        _AllowList(io.BytesIO(payload)).load()
