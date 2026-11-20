"""Check every knowledge-graph quote against the code texts, and write the sign-off sheet.

    python scripts/kg_signoff.py                 # writes docs/kg_signoff.csv and docs/kg_signoff.md
    python scripts/kg_signoff.py --apply FILE    # rows with a name in verified_by become status "verified"

The check is mechanical and only finds problems early: a misquote, a quote from another
document, a wrong page. It does not replace a person reading the clause. Each Requirement
becomes "verified" only when someone fills in verified_by for its row and runs --apply.

Matching: the quote is split at "..." into fragments; each fragment is normalised (lower
case, letters and digits only) and scored by the share of its word 4-grams found in the
source text (OCR noise breaks a few). Page: the printed page numbers (lone numbers on header
or footer lines) or the PDF page around the best match must include the cited page.
"""

import argparse
import csv
import re
import sys
import warnings
from datetime import date
from pathlib import Path

import rdflib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.inference.kg import KG_DIR, SHAPES  # noqa: E402

S = rdflib.Namespace("https://strata.iitg.ac.in/kg#")
SHEET = ROOT / "docs" / "kg_signoff.csv"
REPORT = ROOT / "docs" / "kg_signoff.md"
OCR = ROOT / "data" / "reference_docs" / "gmda" / "ocr"
OCR_OF = {"byelaws-2022.pdf": "unified2022", "amendment-byelaws-2024.pdf": "amd2024",
          "25th-july2025amendments.pdf": "amd2025", "amendment-bye-laws-2020.pdf": "gbcr2020"}
N = 4  # words per n-gram
FOUND, CLOSE = 0.85, 0.5  # share of n-grams: quote found / found with OCR differences
# Checked by eye on the PDF where the OCR could not settle it (2026-11-19). A note is not a sign-off.
CHECKED = {
    "req_exit_door_width": "page 32 confirmed on the PDF (vol 1, PDF page 287); the OCR also has a similar sentence on p. 27",
    "req_exit_door_height": "page 32 confirmed on the PDF (vol 1, PDF page 287)",
    "req_stair_width": "page 49 confirmed: 12.18.1 ends p. 48, 12.18.1.1 starts p. 49 (vol 1, PDF pages 153-154)",
    "req_stair_tread": "page 49 confirmed (vol 1, PDF page 154)",
    "fact_guwahati_wind": "page 105 confirmed on the PDF (vol 1, PDF page 522); the OCR match falls on the Part 6 contents list",
    "fact_guwahati_zone": "page corrected 121 -> 120: Annex K column 1 (A to J) is on p. 120 (vol 1, PDF page 537)",
    "fact_guwahati_z": "page corrected 121 -> 120 (vol 1, PDF page 537)",
    "req_reinforcement_elongation": "IS 13920 cl. 5.3.1 read from the page image",
    "req_reinforcement_proof_stress": "IS 13920 cl. 5.3.1 read from the page image",
    "req_beam_width": "read from the page image (IS 13920 p. 4, PDF page 6)",
    "req_beam_width_depth": "read from the page image (IS 13920 p. 4, PDF page 6)",
    "req_beam_bar": "read from the page image (IS 13920 p. 5, PDF page 7)",
    "req_beam_steel_max": "read from the page image (IS 13920 p. 5, PDF page 7)",
    "req_column_width": "read from the page image (IS 13920 p. 7, PDF page 9)",
    "req_strong_column": "read from the page image (IS 13920 p. 8, PDF page 10)",
    **{k: "IS 456 OCR runs words together here; text and printed page read on the PDF page (printed + 13)" for k in (
        "req_beam_cover", "req_slab_cover", "req_beam_span_depth_cantilever", "req_beam_span_depth_simple",
        "req_beam_span_depth_continuous", "req_slab_span_depth")},
    "req_rc_concrete_grade": "IS 456 Table 5 is a table the OCR garbles; printed p. 15 = PDF page 20",
}
FAR = "the 2022 table at gazette p. 9730 (OCR page p026) has garbled numbers, so the match fell on the 2024 amendment's reprint"
PARKING = "Appendix-I table: OCR splits the cells, page 9798 confirmed"
WINDOW = 5000  # characters either side of a match searched for the printed page number


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _pages(path):
    """[(pdf page number or None, raw text)] for a source file."""
    p = ROOT / path
    if p.suffix == ".txt":
        return [(None, p.read_text(errors="ignore"))]
    for tail, folder in OCR_OF.items():
        if p.name.endswith(tail) and (OCR / folder).is_dir():
            return [(int(f.stem[1:]), f.read_text(errors="ignore")) for f in sorted((OCR / folder).glob("p*.txt"))]
    if not p.exists():
        return []
    import pymupdf
    warnings.filterwarnings("ignore")
    pymupdf.TOOLS.mupdf_display_errors(False)
    with pymupdf.open(p) as d:
        return [(i + 1, d[i].get_text()) for i in range(len(d))]


class Source:
    """All files of one standard as one text, with the character offset of every PDF page."""

    def __init__(self, files):
        self.raw, self.starts = "", []  # starts: (offset, pdf page)
        for f in files:
            for page, text in _pages(f):
                self.starts.append((len(self.raw), page))
                self.raw += text + "\n"
        self.norm, self.at = self._index()
        words = self.norm.split()
        self.grams = {}
        for i in range(len(words) - N + 1):
            self.grams.setdefault(" ".join(words[i:i + N]), i)
        self.word_at = [m.start() for m in re.finditer(r"\S+", self.norm)]
        common = sum(w in ("the", "shall", "of", "and", "be") for w in words)
        self.readable = len(words) > 200 and common / len(words) > 0.03  # a font-encoded text layer has none

    def _index(self):
        """Normalised text and, per normalised character, its offset in the raw text."""
        out, at, space = [], [], True
        for k, ch in enumerate(self.raw.lower()):
            if ch.isascii() and ch.isalnum():
                out.append(ch)
                at.append(k)
                space = False
            elif not space:
                out.append(" ")
                at.append(k)
                space = True
        return "".join(out), at

    def match(self, quote):
        """(share of the quote's n-grams found, raw offset of the matched text or None)."""
        total, hits, where = 0, 0, []
        for frag in re.split(r"\.\.\.|…", quote):
            words = _norm(frag).split()
            if len(words) < N:
                if words and f" {' '.join(words)} " in f" {self.norm} ":
                    total, hits = total + 1, hits + 1
                elif words:
                    total += 1
                continue
            for i in range(len(words) - N + 1):
                total += 1
                k = self.grams.get(" ".join(words[i:i + N]))
                if k is not None:
                    hits += 1
                    where.append(k)
        if not where:
            return (hits / total if total else 0.0), None
        where.sort()
        span = max(40, 2 * len(_norm(quote).split()))  # words: the quote's own length, with room for OCR noise
        best, k, j = 0, where[0], 0
        for i, w in enumerate(where):  # the densest cluster of hits is the quote; a stray phrase elsewhere is not
            while where[j] < w - span:
                j += 1
            if i - j + 1 > best:
                best, k = i - j + 1, where[j]
        return hits / total, self.at[self.word_at[k]]

    def page_near(self, offset):
        """Printed page numbers and PDF pages around a raw offset."""
        lo, hi = max(0, offset - WINDOW), offset + WINDOW
        printed = set()
        for line in self.raw[lo:hi].splitlines():
            s = line.strip()
            if 0 < len(s) <= 70:
                printed |= {int(n) for n in re.findall(r"(?<![\d.])(\d{1,4})(?![\d.])", s)
                            if len(s) <= 6 or re.search(r"(?:^|\s)\d{1,4}\s*$|^\s*\d{1,4}\s", s)}
        pdf = {p for o, p in self.starts if p is not None and lo - 4000 <= o <= hi}
        before = [p for o, p in self.starts if o <= offset and p is not None]
        if before:
            pdf.add(before[-1])
        return printed, pdf


def _cited_pages(text):
    nums = [int(n) for n in re.findall(r"\d+", str(text))]
    if len(nums) == 2 and "-" in str(text):
        return set(range(nums[0], nums[1] + 1))
    return set(nums)


def load_graph():
    g = rdflib.Graph()
    for p in sorted(KG_DIR.rglob("*.ttl")):
        if p.name != SHAPES:
            g.parse(p)
    return g


def check(g):
    sources, rows = {}, []
    for req in sorted(g.subjects(rdflib.RDF.type, S.Requirement), key=str):
        clause = g.value(req, S.citedBy)
        std = g.value(clause, S.inStandard)
        files = sorted(str(f) for f in g.objects(std, S.sourceFile))
        local = [f for f in files if (ROOT / f).exists()]
        quote = str(g.value(clause, S.quote) or "")
        page = str(g.value(clause, S.page) or "")
        value = g.value(req, S.value)
        value = value if value is not None else g.value(req, S.valueText)
        row = {"id": str(req).rsplit("/", 1)[-1], "matrix": str(g.value(req, S.matrixItem) or ""),
               "standard": str(std).rsplit("/", 1)[-1], "clause": str(g.value(clause, S.clauseId)),
               "page": page, "element": str(g.value(req, S.appliesTo)).split("#")[-1],
               "quantity": str(g.value(req, S.quantity)).split("#")[-1],
               "comparator": str(g.value(req, S.comparator)), "value": str(value),
               "unit": str(g.value(req, S.unit) or ""), "status": str(g.value(req, S.status)),
               "quote_check": "", "page_check": "", "note": "", "quote": quote, "verified_by": "", "verified_on": ""}
        rid = row["id"]
        row["note"] = CHECKED.get(rid) or (FAR if rid.startswith("req_far_") else
                                            PARKING if rid.startswith("req_parking_") else "")
        if not local:
            row["quote_check"] = row["page_check"] = "no source file in data/reference_docs"
        else:
            key = tuple(local)
            if key not in sources:
                sources[key] = Source(local)
            src = sources[key]
            share, offset = src.match(quote)
            if not src.readable:
                row["quote_check"] = row["page_check"] = "source text not machine-readable (font-encoded): check by eye"
                rows.append(row)
                continue
            row["quote_check"] = (f"found ({share:.0%})" if share >= FOUND else
                                  f"close ({share:.0%}, OCR differences)" if share >= CLOSE else
                                  f"NOT FOUND ({share:.0%})")
            if offset is None:
                row["page_check"] = "-"
            else:
                printed, pdf = src.page_near(offset)
                cited = _cited_pages(page)
                near = {q + d for q in cited for d in (-1, 1)}
                row["page_check"] = ("page found" if cited & printed else
                                     "PDF page" if cited & pdf else
                                     "adjacent page (OCR puts page numbers out of order)" if near & printed else
                                     f"CHECK: near pages {sorted(p for p in printed if p < 10000)[:6]}")
        rows.append(row)
    return rows


def write(rows, old=None):
    old = old or {}
    for r in rows:  # keep sign-offs already entered in the sheet
        if r["id"] in old:
            r["verified_by"], r["verified_on"] = old[r["id"]]["verified_by"], old[r["id"]]["verified_on"]
    SHEET.parent.mkdir(exist_ok=True)
    with SHEET.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    ok = sum(r["quote_check"].startswith("found") for r in rows)
    close = sum(r["quote_check"].startswith("close") for r in rows)
    pages = sum(r["page_check"] in ("page found", "PDF page") for r in rows)
    flagged = [r for r in rows if r["quote_check"].startswith(("NOT", "no source", "source text")) or r["page_check"].startswith("CHECK")
               or r["note"]]
    lines = [f"# Knowledge-graph sign-off ({date.today().isoformat()})", "",
             f"{len(rows)} requirements. Quote found in the source text: {ok}; found with OCR differences: {close}; "
             f"cited page confirmed: {pages}. Statuses: "
             + ", ".join(f"{s} {sum(r['status'] == s for r in rows)}" for s in ("verified", "transcribed", "assumed"))
             + ".", "",
             "To sign off, read each clause in the code, put your name in `verified_by` (and the date in "
             "`verified_on`) in `docs/kg_signoff.csv`, then run `python scripts/kg_signoff.py --apply "
             "docs/kg_signoff.csv`. Assumed values stay assumed until their clause is found.", "",
             "## Rows to look at first", "", "| id | clause | page | quote | page check | checked by eye |",
             "|---|---|---|---|---|---|"]
    lines += [f"| {r['id']} | {r['clause']} | {r['page']} | {r['quote_check']} | {r['page_check']} | {r['note']} |"
              for r in flagged]
    REPORT.write_text("\n".join(lines) + "\n")
    return ok, close, pages, flagged


def apply(sheet):
    """Set status "verified" for signed rows, in place in the .ttl files (comments and layout kept)."""
    signed = {r["id"]: r for r in csv.DictReader(open(sheet)) if r.get("verified_by", "").strip()}
    done = []
    for path in sorted(KG_DIR.rglob("*.ttl")):
        text = path.read_text()
        for rid, r in signed.items():
            m = re.search(rf"^kg:{re.escape(rid)} a strata:Requirement ;.*?\.\s*$", text, re.S | re.M)
            if not m:
                continue
            block = m.group(0)
            if 'strata:status "assumed"' in block:
                continue  # an assumption is not verified by signing it: find the clause first
            new = block.replace('strata:status "transcribed"', 'strata:status "verified"')
            note = f'verified by {r["verified_by"].strip()}' + (f' on {r["verified_on"].strip()}' if r["verified_on"].strip() else "")
            new = re.sub(r"\s*\.\s*$", f' ;\n    strata:verifiedBy "{note}" .', new) if "verifiedBy" not in new else new
            text = text.replace(block, new)
            done.append(rid)
        path.write_text(text)
    return done


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", metavar="SHEET")
    a = ap.parse_args()
    if a.apply:
        done = apply(a.apply)
        print(f"{len(done)} requirements marked verified")
    else:
        old = {r["id"]: r for r in csv.DictReader(open(SHEET))} if SHEET.exists() else {}
        ok, close, pages, flagged = write(check(load_graph()), old)
        print(f"quotes found {ok}, close {close}, pages confirmed {pages}, rows to look at {len(flagged)}")
        print(f"wrote {SHEET.relative_to(ROOT)} and {REPORT.relative_to(ROOT)}")
