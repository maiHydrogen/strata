# STRATA launcher for a Dynamo Python node (CPython3).
# Paste this whole file into a Python Script node.
# IN[0]: full Windows path of the building JSON (a String node); optional, without it the
#        newest *.building.json in C:\Users\Public\Documents\STRATA is built.
# Also exports <name>.ifc next to the JSON (Revit's IFC4 exporter).
# OUT: the build report, or the error traceback.

import glob
import importlib
import os
import sys
import traceback

import clr

clr.AddReference("RevitServices")
from RevitServices.Persistence import DocumentManager  # noqa: E402
from RevitServices.Transactions import TransactionManager  # noqa: E402

STRATA_LIB = r"C:\Users\Public\Documents\STRATA\revit\STRATA.extension\lib"
if STRATA_LIB not in sys.path:
    sys.path.append(STRATA_LIB)

import strata_builder  # noqa: E402

STRATA_DIR = r"C:\Users\Public\Documents\STRATA"
# Revit's IFC exporter fails inside a Dynamo node evaluation even when every exception is caught, so
# export from Revit itself (File > Export > IFC) into STRATA_DIR. Set True to try it from here.
EXPORT_IFC = False

importlib.reload(strata_builder)  # pick up edits without restarting Dynamo

doc = DocumentManager.Instance.CurrentDBDocument
builder = None
path = None
problems = []


def _close():
    try:
        TransactionManager.Instance.TransactionTaskDone()
    except BaseException:  # noqa: BLE001
        pass


try:
    path = IN[0] if IN and IN[0] else None  # noqa: F821
    if not path:
        found = glob.glob(os.path.join(STRATA_DIR, "*.building.json"))
        if not found:
            raise IOError("No building JSON given in IN[0] and none found in " + STRATA_DIR)
        path = max(found, key=os.path.getmtime)
    TransactionManager.Instance.EnsureInTransaction(doc)
    builder = strata_builder.build_from_file(doc, path)
    TransactionManager.Instance.TransactionTaskDone()
    # Stairs need their own edit scope, which cannot start inside Dynamo's open transaction
    TransactionManager.Instance.ForceCloseTransaction()
    strata_builder.make_stairs(builder)
except BaseException:  # noqa: BLE001 - .NET exceptions too
    problems.append(traceback.format_exc())
    _close()

if builder is not None and EXPORT_IFC:
    try:  # IFC export inside Dynamo's own transaction; <name>.ifc beside the JSON
        TransactionManager.Instance.EnsureInTransaction(doc)
        strata_builder.export_ifc(builder, path, own_transaction=False)
    except BaseException:  # noqa: BLE001
        problems.append("IFC export:\n" + traceback.format_exc())
    _close()

report = ""
if builder is not None:
    try:
        report = "Built from " + path + "\n" + strata_builder.format_report(builder.report)
    except BaseException:  # noqa: BLE001
        problems.append(traceback.format_exc())
OUT = report + ("\n\nERRORS:\n" + "\n".join(problems) if problems else "")
try:  # also on disk, readable from WSL even if Dynamo shows nothing
    with open(os.path.join(STRATA_DIR, "last_build.txt"), "w") as f:
        f.write(OUT)
except BaseException:  # noqa: BLE001
    pass
