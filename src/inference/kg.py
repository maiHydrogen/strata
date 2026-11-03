"""Standards knowledge graph: NBC 2016, IS codes and bye-laws as cited requirements.

The graph lives in data/knowledge_graph/*.ttl (rdflib) and is validated with SHACL, so
no value enters it without its clause, page and quote. Lookups are deterministic SPARQL
plus plain Python condition checks; no LLM is involved.

Precedence: requirements of the place itself and of every jurisdiction containing it
apply (Guwahati < Assam < India). For the same element, quantity and comparator the most
specific jurisdiction governs, because the local bye-law is the binding document. Among
requirements left at that level, the strictest applies.
"""

from dataclasses import dataclass
from pathlib import Path

import pyshacl
import rdflib

ROOT = Path(__file__).resolve().parents[2]
KG_DIR = ROOT / "data" / "knowledge_graph"
QUERY_DIR = Path(__file__).resolve().parent / "queries"
SHAPES = "shapes.ttl"


@dataclass(frozen=True)
class Value:
    element: str
    quantity: str
    comparator: str  # "ge" minimum, "le" maximum, "eq" fixed value or fact
    value: float | None
    text: str | None  # categorical value, e.g. seismic zone "V"
    unit: str | None
    standard: str
    clause: str
    page: str
    quote: str
    status: str  # "transcribed", "verified" or "assumed"
    jurisdiction: str
    note: str | None = None
    assumptions: tuple = ()  # site facts filled into the context to select this value

    @property
    def citation(self):
        return f"{self.standard}, {self.clause}, p. {self.page}"

    def satisfied_by(self, measured):
        if self.comparator == "ge":
            return measured >= self.value - 1e-9
        if self.comparator == "le":
            return measured <= self.value + 1e-9
        return measured == (self.text if self.text is not None else self.value)


def _py(term):
    if term is None:
        return None
    return term.toPython() if isinstance(term, rdflib.Literal) else str(term)


class KnowledgeGraph:
    def __init__(self, directory=KG_DIR):
        self.directory = Path(directory)
        self.graph = rdflib.Graph()
        for path in sorted(self.directory.rglob("*.ttl")):
            if path.name != SHAPES:
                self.graph.parse(path)
        self.shapes = rdflib.Graph().parse(self.directory / SHAPES)
        self._load_conditions()

    def add_turtle(self, text):
        """Add statements (e.g. a draft bye-law layer) and refresh the cached conditions."""
        self.graph.parse(data=text, format="turtle")
        self._load_conditions()

    def validate(self):
        """SHACL violations as messages; empty when the graph conforms."""
        conforms, report, _ = pyshacl.validate(self.graph, shacl_graph=self.shapes)
        if conforms:
            return []
        sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
        return sorted(f"{report.value(r, sh.focusNode)}: {report.value(r, sh.resultMessage)}"
                      for r in report.subjects(rdflib.RDF.type, sh.ValidationResult))

    def query(self, name, **bindings):
        """Run src/inference/queries/<name>.rq; bindings become SPARQL variables."""
        text = (QUERY_DIR / f"{name}.rq").read_text()
        init = {k: rdflib.Literal(v) for k, v in bindings.items() if v is not None}
        result = self.graph.query(text, initBindings=init)
        return [{str(var): _py(row[var]) for var in result.vars} for row in result]

    def _load_conditions(self):
        self.conditions = {}
        for row in self.query("conditions"):
            self.conditions.setdefault(row["req"], []).append(row)

    def _holds(self, req, context, missing):
        for c in self.conditions.get(req, []):
            key = c["paramKey"]
            if key not in context:
                missing.add(key)
                return False
            v = context[key]
            if c["equals"] is not None and str(v) != str(c["equals"]):
                return False
            bounds = ((c["minInclusive"], lambda x, b: x >= b), (c["minExclusive"], lambda x, b: x > b),
                      (c["maxInclusive"], lambda x, b: x <= b), (c["maxExclusive"], lambda x, b: x < b))
            if any(b is not None and not ok(float(v), float(b)) for b, ok in bounds):
                return False
        return True

    def site_facts(self, jurisdiction):
        """Facts about a place (seismic zone, wind speed, climate...) keyed by quantity."""
        return {v.quantity: v for v in self.requirements(jurisdiction, element="site", _facts=False)}

    def requirements(self, jurisdiction, element=None, quantity=None, _facts=True, **context):
        """Requirements in force at `jurisdiction` whose conditions hold for `context`.

        Context keys missing from the call are filled from the place's site facts where one
        matches (e.g. climate), and the Value records that assumption.
        """
        assumed = {}
        if _facts:
            for key, fact in self.site_facts(jurisdiction).items():
                if key not in context:
                    context[key] = fact.text if fact.text is not None else fact.value
                    assumed[key] = fact
        self._missing = set()
        best = {}
        for row in self.query("requirements", siteKey=jurisdiction, elementKey=element, quantityKey=quantity):
            if not self._holds(row["req"], context, self._missing):
                continue
            group = (row["elementKey"], row["quantityKey"], row["comparator"])
            best.setdefault(group, []).append(row)
        values = []
        for rows in best.values():
            deepest = max(r["depth"] for r in rows)
            for r in rows:
                if r["depth"] != deepest:
                    continue
                used = tuple(f"{k} = {context[k]} ({assumed[k].citation}, {assumed[k].status})"
                             for c in self.conditions.get(r["req"], []) if (k := c["paramKey"]) in assumed)
                values.append(Value(
                    element=r["elementKey"], quantity=r["quantityKey"], comparator=r["comparator"],
                    value=float(r["value"]) if r["value"] is not None else None, text=r["valueText"],
                    unit=r["unit"], standard=r["standard"], clause=r["clauseId"], page=str(r["page"]),
                    quote=r["quote"], status=r["status"], jurisdiction=r["jurKey"], note=r["note"],
                    assumptions=used))
        return values

    def lookup(self, quantity, element, jurisdiction="guwahati", comparator=None, **context):
        """The one governing value of `element`.`quantity` at `jurisdiction`.

        Raises LookupError when nothing applies (naming the context the conditions need)
        and ValueError when both a minimum and a maximum apply and `comparator` is unset.
        """
        found = [v for v in self.requirements(jurisdiction, element, quantity, **context)
                 if comparator is None or v.comparator == comparator]
        if not found:
            need = f"; conditions need: {', '.join(sorted(self._missing))}" if self._missing else ""
            raise LookupError(f"no requirement for {element}.{quantity} at {jurisdiction}{need}")
        kinds = {v.comparator for v in found}
        if len(kinds) > 1:
            raise ValueError(f"{element}.{quantity} has {sorted(kinds)} limits; pass comparator=")
        kind = kinds.pop()
        if kind == "ge":
            return max(found, key=lambda v: v.value)
        if kind == "le":
            return min(found, key=lambda v: v.value)
        if len({(v.value, v.text) for v in found}) > 1:
            raise ValueError(f"conflicting facts for {element}.{quantity} at {jurisdiction}")
        return found[0]
