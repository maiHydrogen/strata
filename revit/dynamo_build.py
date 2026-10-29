# STRATA launcher for a Dynamo Python node (CPython3).
# Paste this whole file into a Python Script node.
# IN[0]: full Windows path of the building JSON (connect a String node).
# OUT: the build report, or the error traceback.

import importlib
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

importlib.reload(strata_builder)  # pick up edits without restarting Dynamo

doc = DocumentManager.Instance.CurrentDBDocument
TransactionManager.Instance.EnsureInTransaction(doc)
try:
    OUT = strata_builder.format_report(strata_builder.build_from_file(doc, IN[0]))  # noqa: F821
except Exception:
    OUT = traceback.format_exc()
TransactionManager.Instance.TransactionTaskDone()

