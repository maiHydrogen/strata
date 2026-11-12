# STRATA launcher for a Dynamo Python node (CPython3).
# Paste this whole file into a Python Script node.
# IN[0]: full Windows path of the building JSON (a String node); optional, without it the
#        newest *.building.json in C:\Users\Public\Documents\STRATA is built.
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

importlib.reload(strata_builder)  # pick up edits without restarting Dynamo

doc = DocumentManager.Instance.CurrentDBDocument
builder = None
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
    OUT = "Built from " + path + "\n" + strata_builder.format_report(builder.report)
except Exception:
    OUT = traceback.format_exc()
    TransactionManager.Instance.TransactionTaskDone()
