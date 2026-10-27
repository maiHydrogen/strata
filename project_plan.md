# STRATA — Step-by-Step Project Plan 

**What STRATA is:** a tool that lets a person with no BIM knowledge describe the house they want and get a code-compliant, structurally designed, native Revit BIM model. It closes two gaps:
- **Information gap:** the user doesn't know what a BIM engineer needs. Closed by the info-requirements matrix, a brief chatbot and a standards knowledge graph.
- **Reliability gap:** an LLM alone can't guarantee a correct layout, code compliance or a sound structure. Closed by GA layout optimisation, rule checks, STAAD.Pro design and a deterministic Revit build.

**Principle:** the LLM proposes; engineering tools check, decide and execute. No LLM in structural design, in the building JSON or in the Revit build.

**Fixed scope (decided 2026-10-27):**
- Typology: individual residential houses, G+1 and G+2.
- Reference location: Guwahati (GMDA bylaws). Bylaws are a swappable layer so other cities can be added.
- Five BIM layers: architectural, civil/structural, plumbing, electrical, finishing.
- Target: LOD 200 minimum, tested before moving higher.
- Structural design checked to IS 456, IS 875, IS 1893, IS 13920 using STAAD.Pro (education license).
- Vastu: optional client constraint.
- Users: individuals building a house. Tender/SoW documents are no longer the main input.

**Pipeline:**
1. Brief (LLM chatbot guided by the info matrix) → `brief.json`
2. Standards inference (knowledge graph: NBC, IS, bylaws, with citations) → `spec.json`
3. Layout (LLM + retrieved floor plans as seeds → GA under hard constraints → rule checker → user approves a plan) → `layout.json`
4. Structure (generate `.std` → STAAD analysis + IS design → resize loop; back to step 3 if infeasible) → `staad_results.json`
5. Services and finishes (plumbing, electrical, finishing from room templates + codes)
6. Building JSON (single source of truth for the whole design) → `building.json`
7. Revit build (pyRevit script, plain code, native elements in all 5 layers) → `model.rvt` + Revit-exported `model.ifc` + consistency check against STAAD
8. Report (plain-language compliance with clause citations + structural summary)

**Timeline:** The final BTP submission is on 25th November.

---

## Phase 0 — Environment Setup

- [x] Set up WSL2 + Ubuntu as primary dev environment; clone the STRATA repo inside the Linux filesystem (not `/mnt/c/...`)
- [x] Create Python virtual environment inside WSL2; pin dependency versions in `requirements.txt`
- [x] Set up VS Code with the WSL extension; confirm `code .` opens the project natively in WSL2
- [x] Set up git remote (GitHub/GitLab); confirm push/pull works from WSL2
- [x] Set up SSH access + git pull capability on Param Kamrupa login node
- [x] Write a minimal `CLAUDE.md` at repo root describing the project architecture
- [x] Revit 2027 installed with an education license on the Windows side (IFC from the demo opens via Link IFC)
- [ ] Check Param Kamrupa onboarding docs (`kamrupasupport@iitg.ac.in`) for module-load conventions (CUDA/Python versions) and scratch-storage quota
- [x] STAAD.Pro installed with an education license on the Windows side
- [x] Note the STAAD.Pro version and which IS code versions it supports (IS 1893:2016, IS 13920:2016)
- [ ] Confirm pyRevit (or an alternative: C# add-in, Dynamo Python) supports Revit 2027
- [x] Update `CLAUDE.md` for the new architecture
- [ ] Add `docs/methodology.md`

## Phase 1 — Requirements, Knowledge Base & Plan Library (2–3 weeks)

- [x] Pick one target building typology — G+1/G+2 individual houses, Guwahati reference
- [x] Draft the pre-BIM information requirements matrix (76 items, 5 sources × 5 layers) and share with supervisor
- [ ] Verify every † code value in the matrix against NBC 2016 and the IS code texts
- [ ] Encode GMDA bylaws (setbacks, ground coverage, FAR, height, parking, rainwater harvesting) as a jurisdiction layer
- [ ] Identify the NBC/IS clauses that gate the typology across all 5 layers (room sizes, heights, stairs, ventilation, loads, seismic, water, drainage, electrical)
- [ ] Encode those clauses as RDF triples in a `.ttl` file using `rdflib`, each value with its clause citation
- [ ] Write and test 5–10 SPARQL queries against the graph before wiring it into the pipeline
- [ ] Collect 50–150 Indian plot-based floor plans (30×40 ft, 30×50 ft, etc.) from Google Images, recording source and plot size
- [ ] Convert plan images to structured JSON (rooms, sizes, adjacency, doors) with a vision LLM; manually verify a sample
- [ ] Note copyright limits: plans used as reference/retrieval data only, not redistributed

## Phase 2 — Brief Elicitation (replaces Document Ingestion + Intent Extraction) (2 weeks)

- [x] Build PyMuPDF path for native/digital PDFs — kept as an optional upload path (plot documents, existing sketches)
- [x] Design a structured JSON extraction schema and few-shot prompt; build the Extractor agent — reused as the base for brief extraction
- [ ] Define the brief schema (pydantic) from the matrix's Must and Should items
- [ ] Build the chatbot: asks only for missing Must items in plain language, offers defaults for the rest, confirms a summary with the user
- [ ] Enforce schema-valid output with automatic retries; pin model version, temperature 0, log every prompt and response
- [ ] Build a test set of 20–30 synthetic client briefs, including deliberate omissions
- [ ] Measure brief-completeness accuracy on the test set
- [ ] PaddleOCR + LayoutLMv3 path (`src/ingestion/{ocr,layout}.py`): parked unless uploads need it

## Phase 3 — Standard Inference Engine (1–2 weeks)

- [ ] Wire the brief into the RDF graph from Phase 1
- [ ] Implement missing-parameter resolution via SPARQL, with clause-level traceability on every filled value
- [ ] Apply the jurisdiction layer by location (Guwahati first)
- [ ] Test: deliberately omit a parameter from a sample brief, confirm the engine fills it correctly with the correct citation

## Phase 4 — Layout Generation (3 weeks)

- [ ] Retrieve the most similar plans from the plan library for a given plot and programme
- [ ] LLM proposes 3–5 candidate layouts as JSON, seeded by the retrieved plans
- [ ] Set up DEAP; implement genotype/phenotype structure; seed the population with the LLM candidates
- [ ] Hard constraints (repair operator or feasibility filter, not penalties alone): room minimums, setbacks, ground coverage, door reachability from the entrance, column grid and maximum spans
- [ ] Soft objectives: adjacency preferences, light and ventilation, plumbing stacks aligned across floors, optional Vastu
- [ ] Independent rule checker (separate code path from the GA) that verifies every candidate
- [ ] 2D plan preview with room areas and checks, for user approval and change requests
- [ ] Local dev/debug runs on WSL2; batch runs with larger populations / multiple seeds on HPC CPU nodes once the fitness function is stable

## Phase 5 — Structural Design with STAAD.Pro (3 weeks)

- [ ] Spike: generate a `.std` file for a single-bay G+1 RCC frame (Zone V, IS 875 loads, IS 456 + IS 13920 design), run it in STAAD, parse the results
- [ ] Automate STAAD runs via OpenSTAAD or command line from the pipeline
- [ ] Initial member sizing from IS 456 / IS 13920 rules (columns at wall junctions, beam and slab depths, footings from soil bearing capacity)
- [ ] Load combinations, seismic (IS 1893) and wind (IS 875) load generation in the `.std` file
- [ ] Result parser: utilisation, steel required, pass/fail per member
- [ ] Resize-and-rerun loop until all members pass; send infeasible layouts back to Phase 4 with the constraint that failed
- [ ] Validate one frame against hand calculations

## Phase 6 — Services & Finishes (2 weeks)

- [ ] Plumbing at LOD 200: fixtures per room template, vertical stacks and shafts, overhead tank, sump, septic tank sizing (IS 1172, IS 1742, IS 2470)
- [ ] Electrical at LOD 200: points per room template, distribution board and meter location (IS 732, NBC Part 8)
- [ ] Finishing: wall layer build-ups, floor, wall and ceiling finishes per room type (CPWD specifications)
- [ ] Keep stacks, shafts and services clear of beams and columns from Phase 5

## Phase 7 — Building JSON & Revit Execution (3 weeks)

- [ ] Define the building JSON schema: levels, grids, wall types with layers, walls, hosted doors/windows, floors, roofs, stairs, rooms, columns, beams, footings, fixtures, finishes
- [ ] Spike: pyRevit script on Revit 2027 builds walls, doors, windows, floor and rooms for a 2-room flat from JSON
- [ ] Revit listener: pyRevit Routes, or a folder-watch add-in as fallback
- [ ] Full deterministic builder for all 5 layers with native families and types (no LLM in this step)
- [ ] Write STAAD results into Revit element parameters (utilisation, steel required, check status)
- [ ] Export IFC from Revit; validate it with IfcOpenShell
- [ ] Consistency check: Revit member sizes and positions match the STAAD model

## Phase 8 — Integration & Web App (2 weeks)

- [ ] Orchestrator: one folder per job, one file per step, resumable after a failure
- [ ] Web app (Python, running locally): chat, plan preview and approval, "Generate BIM" with progress, 3D IFC viewer, downloads (RVT, IFC, PDF plans)
- [ ] Expose STRATA as an MCP server (`design_house`, `revise_layout`) so any LLM can call it
- [ ] Full end-to-end test: client brief in → native Revit model + IFC out
- [ ] Single machine, single operator for the BTP; no public deployment

## Phase 9 — Evaluation Protocol (2 weeks)

- [ ] Ablation on the same 20–30 briefs: (A) LLM + Revit directly (`strata_demo`, Text2BIM-style baseline), (B) LLM + knowledge graph, (C) full STRATA
- [ ] Metrics: brief completeness, NBC/bylaw rule pass rate (independent checker), door reachability, STAAD design pass rate, Revit build success, time per design
- [ ] Optional: compare against CPWD standard type designs where plans exist
- [ ] Produce a results table comparable in format to Text2BIM's reported numbers

## Phase 10 — HPC Stretch Goals (parallel, lower priority)

- [ ] Self-host an open-weight LLM (e.g. Gemma, Llama, Qwen) on Param Kamrupa
- [ ] Benchmark self-hosted vs. API on brief extraction and layout proposals: accuracy, latency, cost
- [ ] (Optional) LoRA fine-tune on the plan library as a comparison arm

## Phase 11 — Thesis Writing & Defense Prep (protected buffer)

- [ ] Send the supervisor a one-page scope note: input changed from tenders to client briefs; confirm title and objectives
- [ ] Write thesis sections using Phase 9 results
- [ ] Rebuild the progress slide deck with final results
- [ ] Prepare defence answers: MCP's role (STRATA as the MCP tool), LLM vs deterministic stages, why STAAD, licensing and deployment as future work
- [ ] Hard stop on new feature work before the writing buffer

---

## Future work (ideas from the v1 plan, kept as later upgrades)

These were in the original document-driven plan. They are out of the current scope but are genuine upgrades once the house pipeline works.

- **Organisational input: tender / SoW / DPR documents.** Re-enable the document path for public-works users: PyMuPDF for native PDFs, PaddleOCR + LayoutLMv3 for scanned or complex documents (code already in `src/ingestion/{ocr,layout}.py`, untested).
- **Legal-vs-construction content filter.** Classifier trained on manually tagged reference documents to strip contractual boilerplate (observed ~80% of real tenders).
- **Reference corpus of 15–20 real documents** as few-shot and evaluation set for document extraction accuracy.
- **Coreference / disambiguation pass** over long documents (e.g. "the structure" → the named building).
- **Live MCP co-pilot.** Expose a fixed set of PyRevit tool functions (create_wall, create_door, create_room, …) through an MCP server for interactive edits after the deterministic build.
- **Revit-free IFC export.** The IfcOpenShell builder from `strata_demo` as an open path when Revit isn't available.
- **Node.js / Express backend** connecting to the existing demo website, if a separate production frontend is built.
- **Higher BIM dimensions (4D–10D)** from the original proposal: schedule (4D), cost from CPWD DSR (5D), energy/sustainability (6D), facility management (7D), safety (8D), lean (9D), risk (10D).
