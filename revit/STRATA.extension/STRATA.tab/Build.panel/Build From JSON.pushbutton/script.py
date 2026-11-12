#! python3
# -*- coding: utf-8 -*-
"""Build native Revit elements from a STRATA building JSON."""

from pyrevit import forms, revit

import strata_builder  # from STRATA.extension/lib, added to sys.path by pyRevit

__title__ = "Build From\nJSON"

path = forms.pick_file(file_ext="json", title="Pick a STRATA building JSON")
if path:
    with revit.Transaction("STRATA build from JSON"):
        builder = strata_builder.build_from_file(revit.doc, path)
    strata_builder.make_stairs(builder)  # own edit scope, after the transaction above is committed
    forms.alert(strata_builder.format_report(builder.report), title="STRATA")
