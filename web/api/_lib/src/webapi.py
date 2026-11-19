"""What the web app's later pages need: the cited spec, a background design job, its result, the IFC check.

Plain functions, so the API handlers in web/api stay thin and the logic is tested without a server.
The structure job runs STAAD, which lives on the Windows side of the STRATA workstation: it works when
the API runs there (web/scripts/dev_api.py); a hosted deployment reports `local_only` and the pages
show the saved demo run (web/public/demo/run.json, made by scripts/make_web_demo.py).
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
import uuid

from src.brief.schema import Brief
from src.inference.spec import build_spec

GROUPS = (("building", "Building parts"), ("plot", "Plot and bye-laws"), ("structure", "Structure loads"),
          ("site", "Site facts"), ("services", "Services"), ("materials", "Materials"))


def _cite(e):
    s = e.get("source") or {}
    return {"standard": (s.get("standard") or "").split(" (")[0], "clause": s.get("clause"), "page": s.get("page"),
            "status": s.get("status")}


def _row(e):
    v = e.get("value")
    cmp = {"ge": ">=", "le": "<=", "eq": "="}.get(e.get("comparator"), e.get("comparator") or "")
    return {"what": f"{e.get('element', '').replace('_', ' ')} {e.get('quantity', '').replace('_', ' ')}".strip(),
            "rule": f"{cmp} {v} {e.get('unit') or ''}".strip(), "source": _cite(e), "note": e.get("note"),
            "assumptions": e.get("assumptions", [])}


def spec_view(brief_json: dict) -> dict:
    """The knowledge-graph spec of a brief, grouped for reading: every value with its clause."""
    brief = Brief.model_validate(brief_json).with_defaults()
    spec = build_spec(brief)
    rooms = [{"name": r["name"], "rows": [_row(e) for e in r["requirements"]]} for r in spec.get("rooms", [])]
    groups = []
    for key, title in GROUPS:
        items = spec.get(key) or (spec.get("facts") if key == "site" else None) or []
        rows = [_row(e) for e in items if isinstance(e, dict) and "quantity" in e]
        if rows:
            groups.append({"key": key, "title": title, "rows": rows})
    defaults = sorted(k for k, v in brief.sources.items() if str(v).startswith("default"))
    return {"jurisdiction": spec.get("jurisdiction"), "context": spec.get("context", {}), "rooms": rooms,
            "groups": groups, "checks": spec.get("checks", []), "unresolved": spec.get("unresolved", []),
            "deferred": spec.get("deferred", []), "notes": spec.get("notes", []), "defaults": defaults,
            "counts": {"requirements": sum(len(r["rows"]) for r in rooms) + sum(len(g["rows"]) for g in groups),
                       "cited": sum(1 for r in rooms for x in r["rows"] if x["source"]["clause"])
                       + sum(1 for g in groups for x in g["rows"] if x["source"]["clause"])}}


# ---------------------------------------------------------------- structure jobs (local only)

JOBS: dict = {}
_LOCK = threading.Lock()


def staad_available() -> bool:
    from src.structure.staad_runner import DEFAULT_ENGINE
    return os.path.exists(os.environ.get("STAAD_ENGINE", DEFAULT_ENGINE))


def result_view(run_dir: str, building: dict, structure_stage: dict, layout_name: str, copied=None) -> dict:
    """The structure, services and building summary the Structure, Services and Building pages show."""
    with open(os.path.join(run_dir, "design.json")) as f:
        design = json.load(f)
    sv = building.get("services", {})
    counts = {k: len(building.get(k, [])) for k in ("levels", "columns", "beams", "footings", "walls", "openings",
                                                     "floors", "rooms", "stairs", "fixtures", "pipes", "tanks",
                                                     "circuits", "finishes")}
    return {
        "layout": layout_name, "design": design, "notes": structure_stage.get("notes", design.get("notes", [])),
        "attempts": structure_stage.get("attempts", []), "footings": building.get("footings", []),
        "services": {"checks": sv.get("checks", []), "notes": sv.get("notes", []), "tank": sv.get("tank", {}),
                     "circuits": building.get("circuits", []), "pipes": building.get("pipes", []),
                     "finishes": [{k: v for k, v in f.items() if k != "polygon"} for f in building.get("finishes", [])],
                     "fixtures": building.get("fixtures", []),
                     "rooms": [{"id": r["id"], "name": r["name"], "level": r["level"]} for r in building["rooms"]]},
        "counts": counts, "building": building, "copied_to": copied,
        "building_file": os.path.basename(copied) if copied else f"{layout_name}.building.json",
    }


def start_job(brief_json: dict, layout_json: dict, runner=None, out_root=None, copy_to="default") -> str:
    """Design the structure and build the building JSON for an approved layout, in a background thread.
    Poll `job_status(id)`: rounds arrive as STAAD finishes each one."""
    from src.orchestrator import ROOT, Run, StageError, stage_building, stage_structure
    from src.spatial.layout import Layout
    from src.structure.staad_runner import DEFAULT_WORKDIR, StaadRunner

    brief = Brief.model_validate(brief_json).with_defaults()
    layout = Layout.model_validate(layout_json)
    jid = time.strftime("%m%d%H%M%S") + "-" + uuid.uuid4().hex[:4]
    name = f"web_{jid}"
    layout = layout.model_copy(update={"name": name})
    out = os.path.join(out_root or os.path.join(ROOT, "output", "runs"), name)
    os.makedirs(out, exist_ok=True)
    job = {"id": jid, "state": "running", "stage": "structure", "rounds": [], "started": time.time(), "log": []}
    with _LOCK:
        JOBS[jid] = job

    def tick(layout_name, r):
        job["rounds"].append({"layout": layout_name, "number": r.number, "sections": r.sections,
                              "passed": r.passed, "changes": list(r.changes)[:6]})

    def work():
        run = Run(name, out, log=job["log"].append)
        try:
            spec = build_spec(brief)
            result, final = stage_structure(run, layout, brief, spec, runner or StaadRunner(), on_round=tick)
            job["stage"] = "building"
            building, copied = stage_building(run, final, result, brief, spec,
                                              copy_to=DEFAULT_WORKDIR if copy_to == "default" else copy_to)
            st = next(s for s in run.stages if s["stage"] == "structure")
            job["result"] = result_view(out, building, st, final.name, copied)
            run.manifest("complete", layout=final.name)
            job["state"] = "complete"
        except StageError as e:
            job.update(state="stopped", reason=str(e), advice=e.advice)
            run.manifest("stopped", stopped_at=e.stage, reason=str(e), advice=e.advice)
        except Exception as e:  # noqa: BLE001 - reported to the page
            job.update(state="failed", reason=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-1500:])
        job["seconds"] = round(time.time() - job["started"], 1)

    threading.Thread(target=work, daemon=True).start()
    return jid


def job_status(jid: str) -> dict | None:
    job = JOBS.get(jid)
    if job is None:
        return None
    out = {k: v for k, v in job.items() if k not in ("log",)}
    out["elapsed"] = round(time.time() - job["started"], 1)
    return out


# ---------------------------------------------------------------- IFC check

def ifc_view(ifc_bytes: bytes, building: dict) -> dict:
    import tempfile

    from src.execution.ifc_check import check_ifc
    with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as f:
        f.write(ifc_bytes)
        path = f.name
    try:
        rep = check_ifc(path, building)
    finally:
        os.unlink(path)
    return {"schema": rep.schema, "passed": rep.passed,
            "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in rep.checks],
            "info": rep.info}


# ---------------------------------------------------------------- Revit build (local only)
#
# The STRATA listener (revit/STRATA.extension/startup.py, pyRevit Routes) runs inside Revit on the
# Windows side of the workstation. WSL reaches it on localhost (mirrored networking). Revit builds one
# model at a time, so jobs queue behind a lock.

REVIT_URL = os.environ.get("STRATA_REVIT_URL", "http://127.0.0.1:48884/strata")
REVIT_TIMEOUT = 900  # seconds; a G+2 house builds in about a minute
_REVIT_LOCK = threading.Lock()
BUILDING_NAME = r"^[A-Za-z0-9_.-]+\.building\.json$"
FILE_KINDS = (".building.json", ".rvt", ".ifc")


def workdir() -> str:
    """The shared folder on the Windows side where building JSONs, STAAD files, models and IFCs live."""
    from src.structure.staad_runner import DEFAULT_WORKDIR
    return os.environ.get("STRATA_WORKDIR", DEFAULT_WORKDIR)


def windows_path(path: str) -> str:
    """C:\\... for a /mnt/c/... path (no wslpath call, so it also works in tests)."""
    import re
    m = re.match(r"^/mnt/([a-z])/(.*)$", path)
    return f"{m.group(1).upper()}:\\" + m.group(2).replace("/", "\\") if m else path


def local_path(path: str) -> str:
    """/mnt/c/... for a C:\\... path."""
    import re
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", path or "")
    return f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/") if m else path


def revit_status(timeout=3.0) -> dict:
    """The listener's /status, or {"ok": False, "error": ...} when Revit or pyRevit Routes is not running."""
    import httpx
    try:
        r = httpx.get(f"{REVIT_URL}/status", timeout=timeout)
        return r.json() if r.status_code == 200 else {"ok": False, "error": f"Revit listener answered {r.status_code}"}
    except (httpx.HTTPError, ValueError) as e:
        return {"ok": False, "error": f"Revit is not reachable at {REVIT_URL} ({type(e).__name__})"}


def health() -> dict:
    """What the web app shows in its status light: is this the workstation, and are STAAD and Revit up."""
    from src.brief.llm import DEFAULT_MODEL, GROQ_DEFAULT_MODEL
    rv = revit_status()
    provider = (os.environ.get("STRATA_LLM_PROVIDER") or "gemini").lower()
    model = os.environ.get("STRATA_LLM_MODEL") or (GROQ_DEFAULT_MODEL if provider == "groq" else DEFAULT_MODEL)
    return {"ok": True, "workstation": True, "staad": staad_available(), "revit": bool(rv.get("ok")),
            "revit_template": bool(rv.get("template")), "revit_busy": bool(rv.get("busy")),
            "revit_error": rv.get("error"), "llm": provider, "llm_model": model}


def safe_file(name: str) -> str | None:
    """The full path of a run file in the shared folder, or None: a bare name with a known extension only."""
    import re
    if not name or not re.match(r"^[A-Za-z0-9_.-]+$", name) or not name.endswith(FILE_KINDS):
        return None
    path = os.path.join(workdir(), name)
    return path if os.path.isfile(path) else None


def start_revit(building_file: str, post=None) -> str:
    """Build the native Revit model and IFC for a building JSON in the shared folder, then check the IFC.
    Poll `job_status(id)`. `post(url, json, timeout)` is the HTTP call (replaced in tests)."""
    import re
    if not re.match(BUILDING_NAME, building_file or ""):
        raise ValueError("building_file must be a <name>.building.json")
    path = os.path.join(workdir(), building_file)
    if not os.path.isfile(path):
        raise FileNotFoundError(building_file)
    jid = "rv" + time.strftime("%m%d%H%M%S") + "-" + uuid.uuid4().hex[:4]
    job = {"id": jid, "kind": "revit", "state": "queued", "stage": "waiting for Revit", "started": time.time(),
           "building_file": building_file}
    with _LOCK:
        JOBS[jid] = job

    def call(url, body, timeout):
        import httpx
        r = httpx.post(url, json=body, timeout=timeout)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {"ok": False, "error": r.text[:500]}

    def work():
        with _REVIT_LOCK:
            job.update(state="running", stage="building in Revit")
            try:
                status, out = (post or call)(f"{REVIT_URL}/build", {"building_path": windows_path(path)}, REVIT_TIMEOUT)
                job["report"], job["text"] = out.get("report"), out.get("text")
                if status != 200 or not out.get("ok"):
                    job.update(state="failed", reason=out.get("error") or f"Revit listener answered {status}")
                    return
                job["files"] = {k: os.path.basename(local_path(out[k])) for k in ("rvt", "ifc") if out.get(k)}
                ifc = out.get("ifc") and local_path(out["ifc"])
                if ifc and os.path.isfile(ifc):
                    job["stage"] = "checking the IFC"
                    with open(path) as f:
                        building = json.load(f)
                    with open(ifc, "rb") as f:
                        job["ifc_check"] = ifc_view(f.read(), building)
                job["state"] = "complete"
            except Exception as e:  # noqa: BLE001 - reported to the page
                job.update(state="failed", reason=f"{type(e).__name__}: {e}")
            finally:
                job["seconds"] = round(time.time() - job["started"], 1)

    threading.Thread(target=work, daemon=True).start()
    return jid
