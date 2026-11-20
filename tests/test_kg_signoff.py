"""Knowledge-graph sign-off: quotes are found in the code texts; signing a row marks only that value verified."""

import csv
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import kg_signoff as K  # noqa: E402

from src.inference.kg import KnowledgeGraph  # noqa: E402


@pytest.fixture(scope="module")
def rows():
    return {r["id"]: r for r in K.check(K.load_graph())}


def test_most_quotes_are_found_in_the_source_texts(rows):
    readable = [r for r in rows.values() if not r["quote_check"].startswith(("source text", "no source")) and not r["note"]]
    found = [r for r in readable if r["quote_check"].startswith(("found", "close"))]
    assert len(found) >= 0.9 * len(readable)
    assert all(r["note"] for r in rows.values() if r["quote_check"].startswith("source text"))  # each read by eye
    assert rows["req_plinth_height"]["quote_check"].startswith("found")
    assert rows["req_plinth_height"]["page_check"] in ("page found", "adjacent page (OCR puts page numbers out of order)")


def test_signing_a_row_marks_that_value_verified_and_leaves_assumptions(tmp_path, monkeypatch, rows):
    kg = tmp_path / "kg"
    shutil.copytree(K.KG_DIR, kg)
    monkeypatch.setattr(K, "KG_DIR", kg)
    sheet = tmp_path / "s.csv"
    assumed = next(i for i, r in rows.items() if r["status"] == "assumed")
    with sheet.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows["req_plinth_height"]))
        w.writeheader()
        for rid in ("req_plinth_height", assumed):
            w.writerow({**rows[rid], "verified_by": "A. Engineer", "verified_on": "2026-11-20"})
    assert K.apply(sheet) == ["req_plinth_height"]
    g = KnowledgeGraph(kg)
    assert g.validate() == []
    v = g.lookup("height", "plinth", "guwahati")
    assert v.status == "verified"
    text = "".join(p.read_text() for p in kg.rglob("*.ttl"))
    assert f'kg:{assumed} a strata:Requirement' in text
    block = text.split(f"kg:{assumed} a strata:Requirement")[1].split(" .\n")[0]
    assert 'strata:status "assumed"' in block and "verifiedBy" not in block
