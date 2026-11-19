"""STRATA end to end: brief -> spec -> layouts -> approval -> STAAD design -> services -> building JSON.

The deterministic stages chained in one place, so the CLI (scripts/strata.py), the per-stage scripts
and the web API share them. The LLM is only in the brief chat (before this) and, optionally, in the
layout seeds; everything here checks, decides and executes. The Revit build and the IFC export run
on the Windows side from the building JSON this writes (see revit/dynamo_build.py).

A run can be resumed (`resume=True`, `--resume`): finished stages are loaded from their files instead of
being redone (the STAAD result from structure.pkl), unless the brief changed or a different layout is
picked, which redoes that stage and everything after it. The building JSON is always rebuilt (0.1 s).

Every run writes output/runs/<name>/run.json: stage timings, the files written, what was assumed,
which layout was approved and by whom, the structure rounds, and the services checks.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import shutil
import time
from dataclasses import dataclass, field

from src.execution.building_schema import Building, validate_building
from src.execution.layout_to_building import building_from_layout
from src.inference.spec import build_spec
from src.services.generate import add_services, prefs_from_brief, tank_from_brief
from src.spatial.checker import check_layout
from src.spatial.ga import alternatives, evaluate, suggest, to_layout
from src.spatial.layout import Layout
from src.spatial.preview import render_svg
from src.structure.pipeline import design_structure, layout_feedback, summary
from src.structure.staad_runner import DEFAULT_WORKDIR
from src.structure.std_writer import write_std

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE_NOTE = "IS 456:2000 + IS 13920:2016; loads IS 875 Parts 1-3, seismic IS 1893 (Part 1):2016"


class StageError(RuntimeError):
    """A stage cannot go on; `stage` names it and `advice` says what would help."""

    def __init__(self, stage, message, advice=None):
        super().__init__(message)
        self.stage, self.advice = stage, advice or []


@dataclass
class Run:
    name: str
    out: str
    log: callable = print
    stages: list = field(default_factory=list)
    files: dict = field(default_factory=dict)
    stamp: str = field(default_factory=lambda: time.strftime("%m%d%H%M"))  # keeps runs from overwriting

    def stage(self, name, started, **info):
        entry = {"stage": name, "seconds": round(time.time() - started, 1), **info}
        self.stages.append(entry)
        self.log(f"[{name}] done in {entry['seconds']} s")
        return entry

    def write(self, key, filename, content):
        path = os.path.join(self.out, filename)
        with open(path, "w") as f:
            f.write(content if isinstance(content, str) else json.dumps(content, indent=1))
        self.files[key] = os.path.relpath(path, ROOT)
        return path

    def reuse(self, entry):
        self.stages.append({**entry, "reused": True})
        self.log(f"[{entry['stage']}] reused from the previous run")

    def manifest(self, status, **extra):
        data = {"name": self.name, "status": status, "stamp": self.stamp, "stages": self.stages,
                "files": self.files, **extra}
        path = os.path.join(self.out, "run.json")
        with open(path, "w") as f:
            json.dump(data, f, indent=1, default=str)
        return path


# ---------------------------------------------------------------- stages

def stage_spec(run: Run, brief):
    t = time.time()
    spec = build_spec(brief)
    run.write("spec", f"{run.name}.spec.json", spec)
    assumed = sorted(k for k, v in brief.sources.items() if str(v).startswith("default"))
    run.stage("spec", t, unresolved=[u["quantity"] for u in spec.get("unresolved", [])], brief_defaults=assumed)
    return spec


def stage_layouts(run: Run, brief, spec, top=3, restarts=8, seed=1, max_span=None):
    """Distinct GA layouts, each rule-checked, with an SVG preview. Returns [(layout, check, ga_hard)]."""
    t = time.time()
    kw = {"max_span": max_span} if max_span else {}
    genomes, p = alternatives(brief, spec, count=top, restarts=restarts, seed=seed, **kw)
    options = []
    for k, g in enumerate(genomes, start=1):
        hard, soft, why, _ = evaluate(g, p, detail=True)
        layout = to_layout(g, p, name=f"{run.name}_{run.stamp}_{k}")
        check = check_layout(layout, spec)
        run.write(f"layout_{k}", f"{layout.name}.layout.json", layout.model_dump_json(indent=1))
        run.write(f"check_{k}", f"{layout.name}.check.json", check)
        run.write(f"svg_{k}", f"{layout.name}.svg", render_svg(layout, check))
        options.append({"k": k, "layout": layout, "check": check, "hard": hard, "soft": soft, "why": why})
        run.log(f"  option {k}: {'PASS' if check['pass'] else 'FAIL'} rule check, GA hard {hard:.1f}, "
                f"soft {soft:.3f}" + (f" ({why})" if why else ""))
    run.stage("layout", t, options=[{"k": o["k"], "pass": o["check"]["pass"], "hard": round(o["hard"], 2),
                                     "soft": round(o["soft"], 4)} for o in options])
    if not any(o["check"]["pass"] and o["hard"] <= 0 for o in options):
        tried = suggest(brief, build_spec, seed=seed)
        advice = [f"{r['change']} (fits)" for r in tried if r["fits"]][:4]
        if not advice:  # nothing fits: say how close the best attempts came
            advice = [f"closest: {r['change']}; still short: {', '.join(r['problems'][:2]) or 'layout'}"
                      for r in tried[:2]]
        why = sorted({w for o in options for w in (o["why"] or [])})[:3]
        raise StageError("layout", "no layout meets the hard rules on this plot"
                         + (f" ({'; '.join(why)})" if why else ""), advice)
    return options


def approve(run: Run, options, pick=None, ask=None):
    """The user approves one layout: `pick` (1-based) from the command line, else `ask(options)` (an
    interactive prompt), else the best passing option is taken and recorded as approved automatically."""
    passing = [o for o in options if o["check"]["pass"] and o["hard"] <= 0]
    if pick is not None:
        chosen = next((o for o in options if o["k"] == pick), None)
        if chosen is None or chosen not in passing:
            raise StageError("approval", f"option {pick} is not a passing layout",
                             [f"choose one of {[o['k'] for o in passing]}"])
        how = "user (command line)"
    elif ask is not None:
        chosen, how = ask(passing), "user (prompt)"
    else:
        chosen, how = passing[0], "automatic: best passing option (no approval asked)"
    run.stages.append({"stage": "approval", "option": chosen["k"], "layout": chosen["layout"].name, "by": how})
    run.log(f"[approval] option {chosen['k']} ({chosen['layout'].name}), {how}")
    return chosen["layout"]


def stage_structure(run: Run, layout, brief, spec, runner, attempts=3, rounds=10, seed=1, on_round=None):
    """STAAD design loop; a frame that cannot pass sends a shorter span back to the layout GA.
    `on_round(layout_name, DesignRound)` reports each STAAD round as it finishes."""
    t = time.time()
    tried = []
    for attempt in range(1, attempts + 1):
        def tick(r, name=layout.name):
            run.log(f"  round {r.number}: {r.sections} {'PASS' if r.passed else 'FAIL'}")
            if on_round:
                on_round(name, r)
        result = design_structure(layout, spec, runner, layout.name, brief=brief, max_rounds=rounds, on_round=tick)
        tried.append({"layout": layout.name, "passed": result.passed, "rounds": len(result.outcome.rounds),
                      "reason": None if result.passed else result.outcome.reason})
        if result.passed:
            break
        fb = layout_feedback(result)
        if fb is None:
            raise StageError("structure", f"frame did not pass: {result.outcome.reason}",
                             ["a bigger plot or fewer rooms"])
        run.log(f"  frame did not pass; back to the layout GA: {fb['reason']}")
        relaid = [o for o in _relayout(run, brief, spec, fb["max_span"], layout.name, seed)]
        if not relaid:
            raise StageError("structure", f"no passing layout with spans up to {fb['max_span']} m")
        layout = relaid[0]
        run.write(f"layout_relaid_{attempt}", f"{layout.name}.layout.json", layout.model_dump_json(indent=1))
    else:
        raise StageError("structure", f"structure did not pass after {attempts} layouts")
    info = summary(result)
    info["layout"] = layout.name
    run.write("std", f"{layout.name}.std", write_std(result.outcome.frame, title=layout.name))
    run.write("design", "design.json", info)
    run.write("layout_final", f"{layout.name}.final.layout.json", layout.model_dump_json(indent=1))
    with open(os.path.join(run.out, "structure.pkl"), "wb") as f:  # for resuming without another STAAD run
        pickle.dump(result, f)
    run.files["structure"] = os.path.relpath(os.path.join(run.out, "structure.pkl"), ROOT)
    run.stage("structure", t, layout=layout.name, attempts=tried, sections=info["sections"], slab_mm=info["slab_mm"],
              footings=len(result.footings), notes=result.notes)
    return result, layout


def _relayout(run, brief, spec, max_span, name, seed):
    genomes, p = alternatives(brief, spec, count=3, restarts=6, seed=seed, max_span=max_span)
    for k, g in enumerate(genomes, start=1):
        if evaluate(g, p, detail=True)[0] > 0:
            continue
        layout = to_layout(g, p, name=f"{name}_span{max_span:g}_{k}")
        if check_layout(layout, spec)["pass"]:
            yield layout


def stage_building(run: Run, layout, result, brief, spec, copy_to=DEFAULT_WORKDIR):
    """Building JSON with the services and finishes layers, validated, copied to the Revit side."""
    t = time.time()
    building = building_from_layout(layout, result.model, result.outcome, result.footings, CODE_NOTE,
                                    name=f"STRATA {layout.name}")
    add_services(building, tank_from_brief(brief, spec), prefs_from_brief(brief))
    problems = validate_building(Building.model_validate(building))
    if problems:
        raise StageError("building", "building JSON failed validation: " + "; ".join(problems[:5]))
    path = run.write("building", f"{layout.name}.building.json", building)
    copied = None
    if copy_to and os.path.isdir(copy_to):
        copied = os.path.join(copy_to, os.path.basename(path))
        shutil.copy(path, copied)
    sv = building["services"]
    failed = [c["check"] for c in sv["checks"] if not c["pass"]]
    run.stage("building", t, counts={k: len(building.get(k, [])) for k in
                                     ("columns", "beams", "footings", "walls", "openings", "rooms", "stairs",
                                      "fixtures", "pipes", "tanks", "circuits", "finishes")},
              services_checks=f"{len(sv['checks']) - len(failed)}/{len(sv['checks'])}", services_failed=failed,
              copied_to=copied)
    return building, copied


def _previous(out):
    path = os.path.join(out, "run.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _hash(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _done(prev, stage, *keys):
    """The previous run's entry for a finished stage, if every file it needs is still there."""
    if not prev:
        return None
    entry = next((s for s in prev["stages"] if s["stage"] == stage), None)
    if entry is None or not all(prev["files"].get(k) and os.path.exists(os.path.join(ROOT, prev["files"][k]))
                                for k in keys):
        return None
    return entry


def _load(prev, key, mode="r"):
    with open(os.path.join(ROOT, prev["files"][key]), mode) as f:
        return pickle.load(f) if "b" in mode else f.read()


def run_pipeline(brief, name, runner, out=None, pick=None, ask=None, top=3, restarts=8, seed=1, attempts=3,
                 rounds=10, log=print, copy_to=DEFAULT_WORKDIR, resume=False):
    """Brief (already confirmed) -> building JSON. Returns (run.json path, building or None)."""
    out = out or os.path.join(ROOT, "output", "runs", name)
    os.makedirs(out, exist_ok=True)
    brief_json = brief.model_dump_json(indent=1)
    prev = _previous(out) if resume else None
    if prev and prev.get("brief_hash") != _hash(brief_json):
        log("[resume] the brief changed since the previous run: starting over")
        prev = None
    run = Run(name, out, log)
    if prev:
        run.stamp, run.files = prev.get("stamp", run.stamp), dict(prev["files"])
    run.write("brief", f"{name}.brief.json", brief_json)
    t0 = time.time()
    fresh = prev is None  # once a stage is redone, every later stage is redone too
    try:
        entry = None if fresh else _done(prev, "spec", "spec")
        if entry:
            spec = json.loads(_load(prev, "spec"))
            run.reuse(entry)
        else:
            spec, fresh = stage_spec(run, brief), True

        entry = None if fresh else _done(prev, "layout", *[f"{p}_{o['k']}" for o in
                                                          (_done(prev, "layout") or {}).get("options", [])
                                                          for p in ("layout", "check")])
        if entry and entry.get("options"):
            options = [{"k": o["k"], "layout": Layout.model_validate_json(_load(prev, f"layout_{o['k']}")),
                        "check": json.loads(_load(prev, f"check_{o['k']}")), "hard": o["hard"], "soft": o["soft"],
                        "why": None} for o in entry["options"]]
            run.reuse(entry)
        else:
            options, fresh = stage_layouts(run, brief, spec, top=top, restarts=restarts, seed=seed), True

        entry = None if fresh or ask is not None else _done(prev, "approval")
        if entry and (pick is None or pick == entry["option"]):
            layout = next(o["layout"] for o in options if o["k"] == entry["option"])
            run.reuse(entry)
        else:
            layout, fresh = approve(run, options, pick=pick, ask=ask), True

        entry = None if fresh else _done(prev, "structure", "structure", "layout_final")
        if entry:
            result = _load(prev, "structure", "rb")
            layout = Layout.model_validate_json(_load(prev, "layout_final"))
            run.reuse(entry)
        else:
            result, layout = stage_structure(run, layout, brief, spec, runner, attempts=attempts, rounds=rounds,
                                             seed=seed)
        building, copied = stage_building(run, layout, result, brief, spec, copy_to=copy_to)
    except StageError as e:
        log(f"[{e.stage}] STOPPED: {e}")
        for a in e.advice:
            log(f"  would help: {a}")
        return run.manifest("stopped", stopped_at=e.stage, reason=str(e), advice=e.advice, brief_hash=_hash(brief_json),
                            seconds=round(time.time() - t0, 1)), None
    except Exception as e:  # noqa: BLE001 - e.g. STAAD not reachable: keep what finished, so --resume can go on
        log(f"[error] {type(e).__name__}: {e}")
        run.manifest("failed", reason=f"{type(e).__name__}: {e}", brief_hash=_hash(brief_json),
                     seconds=round(time.time() - t0, 1))
        raise
    next_steps = ["Revit, automatic (Revit 2027 open with the STRATA pyRevit listener): python scripts/build_revit.py "
                  + os.path.basename(run.files["building"]) + "  (or Build in Revit in the web app)",
                  "Revit, by hand instead: new project (Default-Multi-Discipline_Metric), load the concrete column, beam "
                  "and footing families, run the Dynamo node on " + os.path.basename(run.files["building"]),
                  "Revit: File > Export > IFC (IFC4) as " + layout.name + ".ifc in the same folder",
                  "WSL: python scripts/check_ifc.py " + run.files["building"]]
    return run.manifest("complete", layout=layout.name, brief_hash=_hash(brief_json),
                        seconds=round(time.time() - t0, 1), next_steps=next_steps), building
