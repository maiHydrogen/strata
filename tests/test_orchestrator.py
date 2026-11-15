"""Orchestrator: approval rules, the run manifest, and a stage that stops the run cleanly."""

import json

import pytest

from src import orchestrator as O


class _L:
    def __init__(self, name):
        self.name = name


def _opts():
    return [{"k": 1, "layout": _L("x_1"), "check": {"pass": False}, "hard": 0.0, "soft": 0.9},
            {"k": 2, "layout": _L("x_2"), "check": {"pass": True}, "hard": 0.0, "soft": 0.8},
            {"k": 3, "layout": _L("x_3"), "check": {"pass": True}, "hard": 0.0, "soft": 0.7}]


def _run(tmp_path):
    return O.Run("x", str(tmp_path), log=lambda *a: None)


def test_automatic_approval_takes_the_best_passing_option_and_says_so(tmp_path):
    run = _run(tmp_path)
    assert O.approve(run, _opts()).name == "x_2"  # option 1 scores higher but fails the rule check
    assert run.stages[-1]["by"].startswith("automatic")


def test_user_pick_and_prompt_are_recorded(tmp_path):
    run = _run(tmp_path)
    assert O.approve(run, _opts(), pick=3).name == "x_3" and run.stages[-1]["by"] == "user (command line)"
    assert O.approve(run, _opts(), ask=lambda p: p[-1]).name == "x_3" and run.stages[-1]["by"] == "user (prompt)"


def test_picking_a_failing_option_is_refused(tmp_path):
    with pytest.raises(O.StageError) as e:
        O.approve(_run(tmp_path), _opts(), pick=1)
    assert e.value.stage == "approval" and "[2, 3]" in e.value.advice[0]


def test_stopped_run_writes_a_manifest_with_the_reason(tmp_path, monkeypatch):
    def no_fit(run, *a, **k):
        raise O.StageError("layout", "no layout meets the hard rules on this plot", ["a wider plot"])

    monkeypatch.setattr(O, "stage_spec", lambda run, brief: {})
    monkeypatch.setattr(O, "stage_layouts", no_fit)

    class Brief:
        def model_dump_json(self, indent=1):
            return "{}"

    path, building = O.run_pipeline(Brief(), "x", runner=None, out=str(tmp_path), log=lambda *a: None, copy_to=None)
    m = json.load(open(path))
    assert building is None and m["status"] == "stopped" and m["stopped_at"] == "layout"
    assert m["advice"] == ["a wider plot"] and m["files"]["brief"].endswith("x.brief.json")


class _Brief:
    def __init__(self, text="{}"):
        self.text = text

    def model_dump_json(self, indent=1):
        return self.text


@pytest.fixture
def staged(monkeypatch, tmp_path):
    """Stand-in stages that write the same files as the real ones and count their calls."""
    import pickle
    import time
    from test_from_layout import _layout
    calls = []
    lay = _layout()

    def spec(run, brief):
        calls.append("spec")
        run.write("spec", "x.spec.json", {"s": 1})
        run.stage("spec", time.time())
        return {"s": 1}

    def layouts(run, brief, spec, **kw):
        calls.append("layout")
        opts = []
        for k in (1, 2):
            run.write(f"layout_{k}", f"x_{k}.layout.json", lay.model_dump_json())
            run.write(f"check_{k}", f"x_{k}.check.json", {"pass": True})
            opts.append({"k": k, "layout": lay, "check": {"pass": True}, "hard": 0.0, "soft": 1.0 + k, "why": None})
        run.stage("layout", time.time(), options=[{"k": o["k"], "pass": True, "hard": 0.0, "soft": o["soft"]}
                                                  for o in opts])
        return opts

    def structure(run, layout, *a, **kw):
        calls.append("structure")
        run.write("layout_final", "x.final.layout.json", layout.model_dump_json())
        with open(tmp_path / "structure.pkl", "wb") as f:
            pickle.dump({"result": 1}, f)
        run.files["structure"] = str(tmp_path / "structure.pkl")
        run.stage("structure", time.time(), layout=layout.name)
        return {"result": 1}, layout

    def building(run, layout, result, *a, **kw):
        calls.append("building")
        assert result == {"result": 1}
        run.write("building", "x.building.json", {})
        run.stage("building", time.time())
        return {}, None

    for n, fn in (("stage_spec", spec), ("stage_layouts", layouts), ("stage_structure", structure),
                  ("stage_building", building)):
        monkeypatch.setattr(O, n, fn)
    monkeypatch.setattr(O, "ROOT", "/")  # files are recorded relative to ROOT; tmp_path is absolute
    return calls


def _go(tmp_path, brief=None, **kw):
    path, b = O.run_pipeline(brief or _Brief(), "x", runner=None, out=str(tmp_path), log=lambda *a: None,
                             copy_to=None, **kw)
    return json.load(open(path))


def test_resume_reuses_every_finished_stage(staged, tmp_path):
    first = _go(tmp_path)
    assert first["status"] == "complete" and staged == ["spec", "layout", "structure", "building"]
    staged.clear()
    again = _go(tmp_path, resume=True)
    assert staged == ["building"] and again["stamp"] == first["stamp"]
    assert [s["stage"] for s in again["stages"] if s.get("reused")] == ["spec", "layout", "approval", "structure"]


def test_a_different_pick_redoes_approval_and_structure_only(staged, tmp_path):
    _go(tmp_path)
    staged.clear()
    m = _go(tmp_path, resume=True, pick=2)
    assert staged == ["structure", "building"]
    assert next(s for s in m["stages"] if s["stage"] == "approval")["option"] == 2


def test_a_changed_brief_starts_over(staged, tmp_path):
    _go(tmp_path)
    staged.clear()
    _go(tmp_path, brief=_Brief('{"changed": 1}'), resume=True)
    assert staged == ["spec", "layout", "structure", "building"]


def test_a_missing_structure_file_redoes_the_structure(staged, tmp_path):
    _go(tmp_path)
    (tmp_path / "structure.pkl").unlink()
    staged.clear()
    _go(tmp_path, resume=True)
    assert staged == ["structure", "building"]
