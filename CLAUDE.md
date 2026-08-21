# STRATA

Automated extraction of engineering intent from unstructured civil-engineering
documents (SoWs, DPRs, Tenders) into a standard-compliant 3D BIM model.

## Pipeline stages
1. Document Ingestion — src/ingestion/ (PyMuPDF / PaddleOCR + LayoutLMv3)
2. Intent Extraction — src/extraction/ (LLM API, few-shot, structured JSON)
3. Standard Inference — src/inference/ (rdflib + SPARQL over NBC/IS clauses)
4. Constrained Spatial Generation — src/spatial/ (DEAP genetic algorithm)
5. 3D Execution — src/execution/ (PyRevit tool functions via MCP, deterministic
   translation only — never an LLM call in this stage)

## Conventions
- Python, venv-managed, dependencies pinned in requirements.txt
- No LLM calls inside src/spatial/ or src/execution/ — those stages are
  deterministic by design (see docs/methodology.md for why)
- Target building typology for this project: [fill in, e.g. residential G+2]