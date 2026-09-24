# STRATA — Step-by-Step Project Plan

**Assumption:** professor raises no major changes to the proposed method. If changes come back, treat Phases 2–6 below as the ones most likely to need revision; Phases 0, 1, 7, and 8 stay valid regardless of architecture tweaks.

**Timeline discipline:** everything technical should be substantially done by early November 2026, leaving November–December free for thesis writing, defense prep, and DSA/placement practice. If a phase overruns, cut scope (fewer case-study buildings, narrower building typology) rather than compressing the writing/defense buffer.

---

## Phase 0 — Environment Setup (Week 1)

- [x] Set up WSL2 + Ubuntu as primary dev environment; clone the STRATA repo inside the Linux filesystem (not `/mnt/c/...`)
- [x] Create Python virtual environment inside WSL2; pin dependency versions in `requirements.txt`
- [x] Set up VS Code with the WSL extension; confirm `code .` opens the project natively in WSL2
- [x] Set up git remote (GitHub/GitLab); confirm push/pull works from WSL2
- [x] Set up SSH access + git pull capability on Param Kamrupa login node
- [ ] Check Param Kamrupa onboarding docs (`kamrupasupport@iitg.ac.in`) for module-load conventions (CUDA/Python versions) and scratch-storage quota
- [x] Write a minimal `CLAUDE.md` at repo root describing the project architecture (see companion prompts file)
- [ ] Confirm PyRevit + Revit are installed and licensed on the native Windows side (Revit doesn't run on Linux)

## Phase 1 — Reference Corpus & Knowledge Base (Weeks 2–3)

- [ ] Collect 15–20 real reference documents (SoWs/DPRs/Tenders) — few-shot + evaluation set, not a training corpus
- [ ] For each document, manually separate legal/contractual clauses from construction-relevant content (this becomes few-shot examples for the ingestion filter)
- [ ] Pick one target building typology to scope the whole project around (recommend: residential G+2, matches your MCP demo case)
- [ ] Identify the ~20–30 NBC/IS clauses that actually gate that typology (room dimensions, ceiling height, egress width, setback rules, staircase width)
- [ ] Encode those clauses as RDF triples in a `.ttl` file using `rdflib`
- [ ] Write and test 3–5 SPARQL queries against the graph to confirm retrieval works before wiring it into the pipeline

## Phase 2 — Document Ingestion Pipeline (Weeks 3–4)

- [x] Build PyMuPDF path for native/digital PDFs
- [ ] Build PaddleOCR + LayoutLMv3 path for scanned/complex documents (code written in `src/ingestion/{ocr,layout}.py`, lazy-imported; untested — `paddleocr`/`transformers` not yet installed)
- [ ] Build a legal-vs-construction content filter/classifier using the manually-tagged examples from Phase 1
- [ ] Test both paths against the full reference corpus; log failure cases

## Phase 3 — Intent Extraction Agent (Weeks 4–5)

- [x] Design the structured JSON schema (action / object / specification / quantity)
- [x] Write the few-shot prompt template using Phase 1's tagged examples (placeholder synthetic examples for now — swap in real Phase 1 examples once that corpus exists)
- [x] Build the Extractor agent (API wrapper) that returns schema-conformant JSON
- [ ] Build the coreference/disambiguation pass (second read-through of the full document)
- [ ] Evaluate extraction accuracy against manually-annotated ground truth on the reference set

## Phase 4 — Standard Inference Engine (Weeks 5–6)

- [ ] Wire Extractor output into the RDF graph from Phase 1
- [ ] Implement missing-parameter resolution via SPARQL, with clause-level traceability on every filled value
- [ ] Test: deliberately omit a parameter from a sample document, confirm the engine fills it correctly with correct citation

## Phase 5 — Constrained Spatial Generation (Weeks 6–7)

- [ ] Set up DEAP; implement genotype/phenotype structure per the methodology's gene-encoding scheme
- [ ] Write fitness function terms sourced directly from Phase 4's KG output (adjacency, egress, room minimums)
- [ ] Add an explicit "door reachability" constraint — this is your direct rebuttal to the MCP failure case
- [ ] Local dev/debug runs on WSL2 (small population sizes)
- [ ] Batch runs with larger populations / multiple parallel seeds on HPC CPU nodes once the fitness function is stable

## Phase 6 — 3D Execution Bridge (Weeks 7–9)

- [ ] Define the fixed set of PyRevit tool functions (create_wall, create_door, create_room, etc.)
- [ ] Set up the MCP server exposing those tool functions
- [ ] Build the deterministic translator: GA output geometry → sequence of tool calls (no LLM in this step)
- [ ] Export via IFCOpenShell; validate output against the IFC schema
- [ ] Test end-to-end on 2–3 case-study buildings

## Phase 7 — Integration & Demo Website (Weeks 9–10)

- [ ] Wire Node.js/Express backend routing across all five stages
- [ ] Connect the existing demo website frontend to the live backend pipeline
- [ ] Run a full end-to-end test: raw document in → IFC file out

## Phase 8 — Evaluation Protocol (Weeks 10–11)

- [ ] Define concrete metrics: extraction accuracy, standards-retrieval precision, GA constraint-satisfaction rate, IFC export success rate
- [ ] Run evaluation across all case-study buildings
- [ ] Produce a results table comparable in format to Text2BIM's reported numbers (this is what closes your "unfalsifiable claim" gap)

## Phase 9 — HPC Stretch Goals (parallel, lower priority, cut first if time-constrained)

- [ ] Self-host an open-weight LLM (Llama-3-8B or Mistral-7B) on Param Kamrupa
- [ ] Benchmark self-hosted vs. API extraction: accuracy, latency, cost
- [ ] (Optional) LoRA fine-tune on the Phase 1 reference set as a comparison arm

## Phase 10 — Thesis Writing & Defense Prep (November–December, protected buffer)

- [ ] Write remaining thesis sections using Phase 8 results
- [ ] Rebuild/update the progress slide deck with final results
- [ ] Prepare for likely defense questions (start with the MCP dual-role reconciliation and the GA-vs-LLM rationale — these are your most examinable points)
- [ ] Hard stop on new feature work by early November; remaining time goes to writing, defense prep, and DSA practice for December placements
