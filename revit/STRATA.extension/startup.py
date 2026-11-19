# -*- coding: utf-8 -*-
"""STRATA listener: pyRevit Routes endpoints the STRATA server calls, so a design goes from the web app
to a native Revit model and IFC with no one at the screen.

    GET  http://127.0.0.1:48884/strata/status  -> {"ok", "revit", "template", "busy"}
    POST http://127.0.0.1:48884/strata/build   {"building_path": "C:\\...\\<name>.building.json"}
         -> {"ok": true, "report": {...}, "text": "...", "rvt": "...", "ifc": "..."}

pyRevit runs this file when Revit starts (Routes must be on: pyRevit Settings > Routes). Handlers run
inside Revit's API context, one at a time. The model logic is lib/strata_builder.py, the same code the
Dynamo node and the button use; no LLM is involved.
"""

import os
import re
import sys
import traceback

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
if LIB not in sys.path:
    sys.path.append(LIB)

from pyrevit import routes  # noqa: E402

STRATA_DIR = os.environ.get("STRATA_DIR", r"C:\Users\Public\Documents\STRATA")
TEMPLATE = os.path.join(STRATA_DIR, "revit", "STRATA_template.rte")
NAME = re.compile(r"^[A-Za-z0-9_.-]+\.building\.json$")

api = routes.API("strata")
STATE = {"busy": False, "last": None}


def _dismiss_dialogs(sender, args):
    """Answer any dialog Revit raises during a build with its default button, so nothing waits on a click."""
    try:
        args.OverrideResult(1)  # IDOK; harmless where the dialog has no OK
    except Exception:  # noqa: BLE001
        pass


def _fail(status, message, report=None):
    return routes.Response(status=status, data={"ok": False, "error": message, "report": report})


@api.route("/status", methods=["GET"])
def status(uiapp):
    return {"ok": True, "revit": uiapp.Application.VersionName, "template": os.path.exists(TEMPLATE),
            "template_path": TEMPLATE, "busy": STATE["busy"], "last": STATE["last"]}


@api.route("/build", methods=["POST"])
def build(request, uiapp):
    data = request.data if isinstance(request.data, dict) else {}
    path = data.get("building_path") or ""
    folder, name = os.path.split(path)
    if not NAME.match(name) or os.path.normcase(os.path.normpath(folder)) != os.path.normcase(os.path.normpath(STRATA_DIR)):
        return _fail(400, "building_path must be a <name>.building.json in " + STRATA_DIR)
    if not os.path.exists(path):
        return _fail(404, "no such file: " + path)
    if not os.path.exists(TEMPLATE):
        return _fail(503, "STRATA template missing: save a Default-Multi-Discipline_Metric project with "
                          "M_Concrete-Rectangular-Column, M_Concrete-Rectangular Beam and M_Footing-Rectangular "
                          "loaded as " + TEMPLATE)
    import strata_builder
    try:
        reload(strata_builder)  # noqa: F821 - IronPython 2.7 builtin: pick up builder edits without restarting Revit
    except NameError:
        import importlib
        importlib.reload(strata_builder)
    rvt = os.path.join(folder, name.replace(".building.json", ".rvt"))
    STATE["busy"] = True
    hooked = False
    try:
        try:
            uiapp.DialogBoxShowing += _dismiss_dialogs
            hooked = True
        except Exception:  # noqa: BLE001
            pass
        report = strata_builder.build_document(uiapp.Application, TEMPLATE, path, rvt)
        STATE["last"] = name
        return {"ok": True, "report": report, "text": strata_builder.format_report(report),
                "rvt": report.get("rvt"), "ifc": report.get("ifc")}
    except Exception as e:  # noqa: BLE001 - .NET exceptions too; reported to the STRATA server
        return _fail(500, "{0}: {1}".format(type(e).__name__, e) + "\n" + traceback.format_exc()[-1200:],
                     getattr(e, "report", None))
    finally:
        STATE["busy"] = False
        if hooked:
            try:
                uiapp.DialogBoxShowing -= _dismiss_dialogs
            except Exception:  # noqa: BLE001
                pass
