"""The web app's server side: cited spec view, background design job with live rounds, result view."""

import json
import time
from pathlib import Path

import pytest

from src import orchestrator as O
from src import webapi as W
from src.structure.design_loop import DesignRound

ROOT = Path(__file__).resolve().parents[1]
BRIEF = json.load(open(ROOT / "examples" / "g1_40x60.brief.json"))


def test_spec_view_cites_every_requirement():
    v = W.spec_view(BRIEF)
    assert v["counts"]["requirements"] > 50 and v["counts"]["cited"] == v["counts"]["requirements"]
    assert {g["key"] for g in v["groups"]} >= {"building", "plot", "structure", "site"}
    row = v["groups"][0]["rows"][0]
    assert row["rule"] and row["source"]["clause"] and row["source"]["page"]
    assert any(r["name"] for r in v["rooms"]) and v["unresolved"] == []


def _wait(jid, timeout=20):
    t = time.time()
    while time.time() - t < timeout:
        st = W.job_status(jid)
        if st["state"] != "running":
            return st
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_job_reports_rounds_live_and_a_result(monkeypatch, tmp_path):
    from test_from_layout import _building, _layout
    model, building = _building()

    def structure(run, layout, brief, spec, runner, on_round=None, **kw):
        for n, ok in ((1, False), (2, True)):
            on_round(layout.name, DesignRound(n, {"column-0": "300x300"}, {}, ok, ["bigger"] if not ok else [], []))
        run.write("design", "design.json", {"sections": {"column-0": "300x300"}, "slab_mm": 125, "notes": ["n"]})
        run.stage("structure", time.time(), layout=layout.name, attempts=[], notes=["n"])
        return object(), layout

    def build(run, layout, result, brief, spec, copy_to=None):
        run.stage("building", time.time())
        return building, None

    monkeypatch.setattr(O, "stage_structure", structure)
    monkeypatch.setattr(O, "stage_building", build)
    jid = W.start_job(BRIEF, _layout().model_dump(mode="json"), runner=object(), out_root=str(tmp_path), copy_to=None)
    st = _wait(jid)
    assert st["state"] == "complete" and [r["number"] for r in st["rounds"]] == [1, 2]
    assert st["rounds"][0]["changes"] == ["bigger"] and st["rounds"][1]["passed"]
    res = st["result"]
    assert res["design"]["slab_mm"] == 125 and res["counts"]["columns"] == len(building["columns"])
    assert res["building"] is building and res["services"]["rooms"]


def test_failed_job_says_why(monkeypatch, tmp_path):
    from test_from_layout import _layout

    def boom(*a, **k):
        raise FileNotFoundError("STAAD engine not found")

    monkeypatch.setattr(O, "stage_structure", boom)
    st = _wait(W.start_job(BRIEF, _layout().model_dump(mode="json"), runner=object(), out_root=str(tmp_path)))
    assert st["state"] == "failed" and "STAAD engine not found" in st["reason"]


def test_unknown_job_is_none():
    assert W.job_status("nope") is None
