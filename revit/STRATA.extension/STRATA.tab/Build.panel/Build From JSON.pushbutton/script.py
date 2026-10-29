#! python3
# -*- coding: utf-8 -*-
"""Build native Revit elements from a STRATA building JSON."""

from pyrevit import forms, revit

import strata_builder  # from STRATA.extension/lib, added to sys.path by pyRevit

__title__ = "Build From\nJSON"

path = forms.pick_file(file_ext="json", title="Pick a STRATA building JSON")
if path:
    with revit.Transaction("STRATA build from JSON"):
        report = strata_builder.build_from_file(revit.doc, path)
    forms.alert(strata_builder.format_report(report), title="STRATA")
