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
- Vastu: future scope (decided 2026-11-11); the brief still records the client's preference.
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
- [x] Confirm pyRevit (or an alternative: C# add-in, Dynamo Python) supports Revit 2027 — pyRevit 7 on Revit 2027: Routes listener builds models (IronPython engine)
- [x] Update `CLAUDE.md` for the new architecture
- [ ] Add `docs/methodology.md`

## Phase 1 — Requirements, Knowledge Base & Plan Library (2–3 weeks)

- [x] Pick one target building typology — G+1/G+2 individual houses, Guwahati reference
- [x] Draft the pre-BIM information requirements matrix (76 items, 5 sources × 5 layers) and share with supervisor
- [ ] Verify every † code value in the matrix against NBC 2016 and the IS code texts — mechanical check done (`scripts/kg_signoff.py`): every quote is matched against the code text (OCR for NBC and the bye-laws, the PDF text for IS 456) and its cited page located; 142 of 159 quotes found, the rest are tables the OCR garbles or IS 13920, whose PDF text is font-encoded, all read by eye on the PDF page and noted in the sheet; one page corrected (Annex K seismic zone, p. 121 -> 120). Sign-off sheet `docs/kg_signoff.csv` (+ `docs/kg_signoff.md`): a person puts a name in `verified_by` and runs `--apply`, which marks those values verified (assumed values stay assumed). Encoded: D1–D10, D12–D16, D18; D11 masonry stays an engineering default (IS 1905 not in hand), D17 (Eco Niwas Samhita) not encoded (text not in hand, not enforced for houses)
- [x] Small-plot setbacks, bye-law 26(3)(iv) N.B. (c): 0.9 m sides on 53.56-93.73 m2, nil one side with the neighbour's NOC on 93.73-134 m2, applied to G+1 only — interpretation to confirm with the supervisor (marked assumed)
- [x] Encode the Guwahati bye-laws as a jurisdiction layer — the governing text is the Assam Unified Building Construction (Regulation) Byelaws 2022 (amended 2024, 2025, 2026), which replaced the GMDA 2014 bye-laws under the Assam Building Construction (Regulation) Act; setbacks, minimum plot, FAR, parking, rainwater harvesting encoded (`data/knowledge_graph/local/`). No residential coverage % (setbacks govern); height limits start above G+3. FAR marked assumed: the 2025 amendment retitled the 26(a) table to commercial/institutional
- [x] Identify the NBC/IS clauses that gate the typology across all 5 layers — architectural (room sizes, heights, stairs, ventilation, courtyard, exits and corridors, travel distance 30 m (Part 4 Table 5, checked by the rule checker), parapet, plinth, entrance ramp), civil (loads, seismic, wind, concrete grade, IS 456 covers, column steel 0.8–6%, 12 mm bars, span/depth ratios, IS 13920 beam width 200 mm, column 300 mm, beam steel 2.5%, strong column 1.4), plumbing (water demand, persons per dwelling, OHT 33–50% of a day, septic tank 750 mm wide, 1 m deep, 1 m3, length 2–4 x width, 100 mm drains, 50 mm vent), electrical (light sub-circuit 10 points / 800 W, lighting <= 13 W/m2, power >= 55 W/m2, sockets above 200 mm, lightning by risk assessment). Tests hold the structure and services constants to the graph's values (`tests/test_structure_rules.py`, `tests/test_services_rules.py`); STAAD now gets the IS 456 covers (beams 30 mm, columns 40 mm) instead of its 25 mm beam default — to confirm on the next STAAD run
- [x] Encode those clauses as RDF triples in a `.ttl` file using `rdflib`, each value with its clause citation — NBC 2016 per supervisor (not NBCS 2026); every value carries clause, printed page, quote and status, enforced by SHACL (`data/knowledge_graph/`)
- [x] Write and test 5–10 SPARQL queries against the graph before wiring it into the pipeline — 8 queries in `src/inference/queries/`, API in `src/inference/kg.py`
- [x] Collect floor plans — ResPlan (17,000 South Asian residential plans, vector, CC BY 4.0) instead of image scraping; 8,500 converted to the layout JSON after scale and geometry checks (`scripts/build_plan_library.py`, `src/spatial/library.py`)
- [x] Small set of Indian 30×40 / 30×50 ft plot-house plans as a local reference (ResPlan has no plot sizes or orientation) — four hand-drawn plans, drawn for STRATA rather than copied: 30×40 G+1, 30×50 G+1, 30×50 G+2 (`src/spatial/reference_plans.py`) and the 40×60 G+1 sample; each passes every checker rule with no warning; layouts and previews in `output/reference_plans/`
- [x] Convert plans to structured JSON (rooms, sizes, adjacency, doors) — not needed as images: ResPlan is already vector; rooms grown to wall centrelines, entrance rotated to the front, open-plan connections kept as openings; every plan run through the rule checker (2,148 with no validity failure)
- [x] Note copyright limits — ResPlan is CC BY 4.0 (cited in the index); the download and derived library are git-ignored and not redistributed

## Phase 2 — Brief Elicitation (replaces Document Ingestion + Intent Extraction) (2 weeks)

- [x] Build PyMuPDF path for native/digital PDFs — kept as an optional upload path (plot documents, existing sketches)
- [x] Design a structured JSON extraction schema and few-shot prompt; build the Extractor agent — reused as the base for brief extraction
- [x] Define the brief schema (pydantic) from the matrix's Must and Should items — `src/brief/schema.py`; matrix defaults applied with their source recorded
- [x] Build the chatbot: asks only for missing Must items in plain language, offers defaults for the rest, confirms a summary with the user — `src/brief/chat.py` (LLM only extracts facts; questions, units, merging and summary are code), terminal `scripts/brief_chat.py`, web app `web/` (Next.js + Vercel Python function)
- [x] Enforce schema-valid output with automatic retries; pin model version, temperature 0, log every prompt and response — Gemma 4 26B (free tier), 3 retries with the error, client-side rate limiter, numeric bounds checked in pydantic (Gemma's constrained decoding stalls on them)
- [x] Second LLM provider: Groq (free Developer plan, gpt-oss-120b / gpt-oss-20b / qwen3.8-27b) beside Gemini — `GroqClient` and `make_client` in `src/brief/llm.py`, chosen by STRATA_LLM_PROVIDER or `eval_briefs.py --provider groq --model ...`; strict structured outputs (schema-constrained decoding), the same pydantic checks and retries; client pacing includes Groq's 200K tokens/day per model
- [x] Build a test set of 20–30 synthetic client briefs, including deliberate omissions — 25 cases in `evaluation/brief_test_set.json`
- [x] Measure brief-completeness accuracy on the test set — final run: extraction recall 98.7%, precision 100%, 0 wrong or invented values, exact omission detection 92%, 23/25 briefs complete and correct (baseline before fixes: 96.9%, 97.8%, 5 wrong, 84%, 20/25); `scripts/eval_briefs.py`, results in `output/eval/`
- [ ] PaddleOCR + LayoutLMv3 path (`src/ingestion/{ocr,layout}.py`): parked unless uploads need it
- [x] Plan-form wishes and plain-words fallbacks in the brief: a central courtyard or open verandah, a stair to the terrace, a symmetrical plan (code rules plus the preferences call, shown in the brief panel and summary); plot sides ("60 x 80 square feet"), front road width and "ground floor only" read by code when the model misses them ("ground floor only", "G+1 only" and "no more floors" also design the structure for the storeys built now, not the A2 default of one more); a road over 30 m refused with a message (a misread 20 ft as 200 ft had set a 7.5 m front setback)

## Phase 3 — Standard Inference Engine (1–2 weeks)

- [x] Wire the brief into the RDF graph from Phase 1 — `src/inference/spec.py`, `scripts/make_spec.py`
- [x] Implement missing-parameter resolution via SPARQL, with clause-level traceability on every filled value — unresolved and layout-deferred values listed explicitly (water demand needs city population; parking needs carpet area)
- [x] Apply the jurisdiction layer by location (Guwahati first) — most specific jurisdiction governs; cities without a layer fall back to NBC 2016
- [x] Test: deliberately omit a parameter from a sample brief, confirm the engine fills it correctly with the correct citation — `tests/test_spec.py`

## Phase 4 — Layout Generation (3 weeks)

- [x] Retrieve the most similar plans from the plan library for a given plot and programme — `retrieve()` (fits the envelope, ranks by room counts then fill); `floor_compatibility()` pairs floors by stair position, footprint and wall lines so beams and columns continue (only 10 of ~49k stair-plan pairs pass: upper floors will mostly come from the GA)
- [x] LLM proposes 3–5 candidate layouts as JSON, seeded by the retrieved plans — `src/spatial/proposals.py`: one Gemma call per design focus, a sketch in the GA's own terms (fixed keys per room, no open-ended lists: Gemma looped on lists), the 3 most similar library plans described in the prompt, mapped exactly onto the genome; `scripts/eval_seeding.py`: 7 of 8 sketches broke NBC rules on their own, and seeding gave a better first generation but no better final layout than random starts (40x60: 3.99 vs 3.56; 30x50 G+2: 3.17 vs 2.81), so it is off by default (`make_layout.py --llm-seeds`)
- [x] Set up DEAP; implement genotype/phenotype structure; seed the population with the LLM candidates — `src/spatial/ga.py`: random-key genome decoded into a "bands and spine" plan (side spine on plots under 6.4 m wide, with shallower bands and side-carved attached baths); when a brief cannot fit, `suggest()` tries brief changes (one more storey, kitchen with dining, attached bath to common) and reports which fit (30x50 ft example: G+2 with kitchen and dining); `run(..., seeds=...)` accepts seed genomes, LLM candidates still to be converted into seeds
- [x] Hard constraints (repair operator or feasibility filter, not penalties alone): room minimums, setbacks, ground coverage, door reachability from the entrance, column grid and maximum spans — setbacks, reachability, the column grid (identical on all floors) and stair alignment hold by construction; band depths repaired to the envelope; room sizes, stair fit, windows, overflow and FAR as heavy penalties
- [x] Soft objectives: adjacency preferences (weights from the plan library's door-adjacency statistics), light and ventilation, plumbing stacks aligned across floors — comfortable room sizes, proportions, kitchen next to dining, baths over wet rooms, a common bath on every bedroom floor, one bedroom downstairs, little wasted space or stair area
- [x] Independent rule checker (separate code path from the GA) that verifies every candidate — `src/spatial/checker.py`: room area/width/height, programme, setbacks, overlaps, walls (thickness, coverage, hanging), doors (shared wall, redundancy, widths, main exit door, WC not into kitchen), windows (external wall, opening ratio, bath ventilator), corridor width, stairs, reachability, column and stair continuity, spans, carpet/built-up area and FAR, materials; each result cites its clause or is marked validity/practice; layout schema `src/spatial/layout.py`
- [x] 2D plan preview with room areas and checks, for user approval and change requests — SVG preview (`src/spatial/preview.py`) in the web app's Layout screen (`web/api/layout.py`): 3 options, checks passed, areas, room schedule, approve (downloads layout.json), generate again, and "try this" for brief changes when nothing fits; free-text change requests (`src/spatial/revise.py`): one LLM call turns the sentence into at most four edits over the plan's own room names (move to a floor, bigger/smaller, next to/away from, swap, terrace end, move a door into a neighbouring room, or "unsupported"; the prompt lists which rooms share a wall so "the bath next to bedroom 4" resolves to a room name), code refuses rule-breaking edits with the spec's clause, the GA re-runs seeded from the approved option with the edits as fixed floors, swapped genes and soft wishes, and each new option reports which edits it meets; the checker still gates every option
- [x] Courtyard plan type (`src/spatial/court.py`): rooms round a central court open to the sky, a 1.5 m open verandah ringing it (no walls between its strips or to the court; Revit bounds it with room separation lines), living room at the front, equal side bays (symmetric frame), optional rear row, symmetry as a soft objective; chosen when the client asks for a courtyard or the plot is too wide for rooms either side of one passage; court size checked in the GA and by the checker against NBC 2016 Part 3 cl. 8.2.5 (a) (3 m minimum width, area >= (H/5)^2), added to the knowledge graph; the 60x80 ft single-storey brief gives three passing options, passes STAAD in 5 rounds, and builds in Revit with IFC 21/21
- [x] Distinct options: duplicates removed by arrangement (room kinds per cell), not by names or centimetres; extra GA restarts while fewer than three options meet every hard constraint; the GA's own reasons ("left row spans 6.1 m") shown on the Layout page in plain words
- [x] Windows clear of columns: windows go only on wall stretches 350 mm clear of every column centre (half a 450 mm column plus a jamb), 200 mm from wall ends and 700 mm from the main door; a wall split by a column gets two smaller windows; the GA's window-capacity constraint uses the same free stretches, and the checker fails any window that crosses a column (validity)
- [ ] Local dev/debug runs on WSL2; batch runs with larger populations / multiple seeds on HPC CPU nodes once the fitness function is stable

## Phase 5 — Structural Design with STAAD.Pro (3 weeks)

- [x] Spike: generate a `.std` file for a single-bay G+1 RCC frame (Zone V, IS 875 loads, IS 456 + IS 13920 design), run it in STAAD, parse the results
- [x] Automate STAAD runs via OpenSTAAD or command line from the pipeline (SProStaad.exe called from WSL, headless)
- [x] Initial member sizing from IS 456 / IS 13920 rules (columns at wall junctions, beam and slab depths, footings from soil bearing capacity): beams span/12, slab IS 456 cl. 24.1, columns from tributary load (cl. 39.3), footings from STAAD service reactions on the brief's SBC (+50% for EQ, IS 1893 cl. 6.3.5.2), punching and one-way shear; overlapping pads combined
- [x] Load combinations (IS 456 / IS 1893) and seismic (IS 1893) load generation in the `.std` file
- [x] Wind load (IS 875 Part 3) and the IS 1893 100% + 30% orthogonal combination (storey forces on the windward joints, Vb 50 m/s from the KG, terrain category 3 assumed; 37 factored and 5 service combinations)
- [x] Result parser: utilisation, steel required, pass/fail per member, member-level errors
- [x] Resize-and-rerun loop until all members pass (steel-first for IS 13920 cl. 7.2; G+1 test house passes in 8 rounds, uniform 400x400 columns)
- [x] Layout to frame (`src/structure/from_layout.py`): grid on wall centrelines, plinth beams, columns only under each storey's own cells (terraces become roofs with parapets), walls per beam net of openings, partitions off the grid as IS 875 Part 2 cl. 3.1.2 slab loads, stair cell loads, a stair cabin (mumty) storey over the stair well; all three 40x60 G+1 GA layouts and the 30x50 G+2 layout pass in STAAD in 5 rounds
- [x] Design for the storeys the client plans later (brief.floors_future): STAAD runs the frame with the top floor repeated, sizes and results carry to the as-built frame, footings take the future load (`src/structure/pipeline.py`)
- [x] Send infeasible layouts back to Phase 4 with the constraint that failed (`layout_feedback`: a shorter maximum span for the GA, rerun up to 3 times; `scripts/design_structure.py`)
- [x] Size columns that stop under a terrace separately: columns with no column above them below the main roof form their own group (`column-<storey>-edge`), so IS 13920 cl. 7.2.1 at their single-column roof joints grows only them; 40x60 G+1 in STAAD: ground columns 350x350, terrace-edge columns 450x450, 5 rounds (before: all ground columns 450x450); footings sized for the largest ground column
- [x] Per-joint strong-column/weak-beam resizing from STAAD's `<name>_13920.txt` joint report (roof joints checked: IS 13920 cl. 7.2.1.3 waives them only above 4 storeys); bigger column bars (MINMAIN) before bigger sections
- [x] IS 456 cl. 26.5.3.1(a) minimum column steel on the gross area (STAAD applies the 0.8% to the concrete area required)
- [x] Sizing order and practice rules: at failing joints column bars, then shallower lightly reinforced beams, then bigger columns; lower-storey columns never smaller than the ones above; beam bars capped at column size / 20 (IS 13920 cl. 7.1.1(a), STAAD MAXMAIN)
- [ ] Validate one frame against hand calculations (dead load, seismic weight, base shear and period already match; member design still to check)

## Phase 6 — Services & Finishes (2 weeks)

- [x] Plumbing at LOD 200: fixtures per wet-room template (NBC Part 9 Sec 2 cl. 4.2.4.1, Sec 1 cl. 3.1.5), soil and waste stacks on the external wall face open to the ground (two-pipe), overhead tank on the stair cabin roof sized from the household (Sec 1 cl. 4.1, 4.4.10) with its weight in the STAAD frame; `src/services/` — sump and septic tank sizing (IS 1172, IS 2470) still open
- [x] Electrical at LOD 200: lights, fans, 6 A/16 A points, switch board per room, distribution board per floor, sub-circuits within NBC Part 8 Sec 2 cl. 5.4.4.3, load densities checked against cl. 5.9.2; AC/TV/geyser points and extra sockets from the brief (A12, asked once as an optional question), socket heights per cl. 7.2.3 with accessible heights (800-1100 mm, 600 mm from corners) when the household has elderly or wheelchair users; points per room still STRATA defaults (assumed); meter location open
- [x] Finishing: floor, wall, dado, skirting and ceiling per room type — client's floor finishes, bathroom tile height and paint from the brief, gaps from the finish level (A11: economy/standard/premium tiers, marked assumed); finish floors on the slab and Revit room finish parameters
- [x] Keep stacks, shafts and services clear of beams and columns from Phase 5: each stack slides along its wall, nearest position first, until it is 75 mm clear of every designed column and 40 mm of every beam (beams are flush with the wall face), and the services report checks it (practice, assumed)

## Phase 7 — Building JSON & Revit Execution (3 weeks)

- [x] Define the building JSON schema: levels, grids, wall types with layers, walls, hosted doors/windows, floors, roofs, stairs, rooms, columns, beams, footings, fixtures, finishes (v0.5 adds rooms with type and outline, fixtures, stacks, tank, circuits and finishes; v0.4 has levels incl. foundation and stair-cabin roof, wall types, walls incl. parapets and the stair cabin, openings, floors with stair openings, rooms, dog-leg stairs (runs + landing), columns, beams, footings; built from the layout and the designed frame by `src/execution/layout_to_building.py`; the stair cabin is in the STAAD frame)
- [x] Spike: script on Revit 2027 builds walls, doors, windows, floor and rooms for a 2-room flat from JSON (run via Dynamo; pyRevit button also written)
- [x] Revit listener: pyRevit Routes — run in Revit 2027 (pyRevit 7) on 19 Nov: web_1115050457-b3d4 (G+1, 48 columns, 69 beams, 13 footings) built in 47 s with no one at the screen, IFC 17/17; `revit/STRATA.extension/startup.py` (`/strata/status`, `/strata/build`: new project from `STRATA_template.rte`, build, stairs, IFC, save .rvt, dialogs answered automatically), `src/webapi.py` `start_revit` + `/api/revit`, `/api/files`, `/api/health`; tested against a mock listener
- [x] Full deterministic builder for all 5 layers with native families and types (no LLM in this step) — structure, walls, openings, slabs, rooms, stairs and pipes native; fixtures and the tank as typed DirectShapes (no MEP families to load), IFC-mapped
- [x] Write STAAD results into Revit element parameters (utilisation, steel required, check status) — shared instance parameters STRATA Member / Design Status / Utilisation / Steel / Design Basis on columns, framing and foundations (Structural Analysis group), Mark = member id, values decided in Python (`design.utilisation`, `design.steel` in the building JSON), exported in the IFC property sets
- [x] Export IFC from Revit; validate it with IfcOpenShell (`scripts/check_ifc.py`: counts per class, openings in walls, storeys, services found by id and typed; the 40x60 house passes 11/11 before services)
- [x] Consistency check: Revit member sizes and positions match the STAAD model — in `src/execution/ifc_check.py`: every column, beam and footing found by member id, section within 2 mm, position within 10 mm (IfcOpenShell geometry), STAAD result present; 40x60 G+1 (19 Nov): 130/130 on all four, IFC 21/21; `scripts/build_revit.py` runs build + check from WSL

## Phase 8 — Integration & Web App (2 weeks)

- [x] Orchestrator: one folder per job, one file per step, resumable after a failure — `scripts/strata.py` / `src/orchestrator.py` chain brief → spec → layouts → approval → STAAD → services → building JSON with a run.json manifest (40x60 brief: 38 s); `--resume` reuses finished stages (STAAD result kept in structure.pkl), a changed brief or a different `--pick` redoes from that stage
- [x] Web app (Python, running locally): chat, plan preview and approval, "Generate BIM" with progress, 3D IFC viewer, downloads (RVT, IFC, PDF plans) — Brief, Standards (cited spec), Layout (approval), Structure (live STAAD rounds), Services, Building JSON (download) and Revit model (steps + IFC upload check) pages; STAAD stages need the local API, a hosted build shows the saved demo run; 3D viewer dropped for now
- [ ] Expose STRATA as an MCP server (`design_house`, `revise_layout`) so any LLM can call it
- [x] Robustness run on 14 varied briefs (`scripts/robustness.py`): no crashes; stopped runs report brief changes that fit or the closest attempt
- [x] Full end-to-end test: client brief in → native Revit model + IFC out — 40x60 brief: building JSON in 38 s, Dynamo build, Revit IFC export passes 17/17 checks (Revit and IFC steps manual)
- [ ] Single machine, single operator for the BTP; no public deployment — hosted demo chain: Vercel -> ngrok -> workstation API (STAAD + Revit on student licences); browser calls the workstation directly (`NEXT_PUBLIC_STRATA_WORKER_URL`, CORS, access code), falls back to the Vercel functions and the demo run; status light in the menu; `scripts/demo_up.sh`, `docs/demo_runbook.md`

## Phase 9 — Evaluation Protocol (2 weeks)

- [ ] Ablation on the same 20–30 briefs: (A) LLM + Revit directly (`strata_demo`, Text2BIM-style baseline), (B) LLM + knowledge graph, (C) full STRATA — `scripts/eval_ablation.py` (`src/evaluation/`): gold briefs from the 25 test cases; A = the `strata_demo` agents run unchanged with only the LLM call swapped (same Gemma model in every arm), its IFC read back into a layout; B = the LLM draws the whole plan from the brief + cited spec, no GA or checker feedback; C = the orchestrator. C done on all 25: 4 delivered (every rule + STAAD + valid building JSON), 20 stopped with brief changes that fit, 1 refused (Shillong: no bylaw layer), no design delivered that breaks a rule. A and B on 10 briefs: free-tier Gemma did not finish one arm-A brief in 2 h (503s, coder reply > 30 min), so they move to Groq (`--provider groq`, gpt-oss-120b) once the key is in `.env`. Write-up: `docs/phase9_evaluation.md`
- [x] Metrics: brief completeness, NBC/bylaw rule pass rate (independent checker), door reachability, STAAD design pass rate, Revit build success, time per design — one scorer for all arms (checker pass, code checks passed, reachability, programme met, STAAD, building JSON, IFC errors, time, LLM calls); brief completeness from the brief eval (20/25 complete and correct); Revit builds on a subset, by hand
- [x] Optional: compare against CPWD standard type designs where plans exist — no CPWD type designs for private plots in the reference docs; compared with the hand-drawn reference plans instead (`scripts/eval_reference.py`): on 40x60 both pass with similar circulation share (25% vs 26%); on the 30-ft plots the hand plans pass and the GA finds none (its narrow-plot "side spine" form makes bands too shallow) — a finding for the thesis, GA fix as future work
- [ ] Produce a results table comparable in format to Text2BIM's reported numbers — `output/eval/ablation_table.md` (arms x metrics, same 10 briefs + every brief), per-brief CSV; final once A and B finish

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

- Narrow-plot plan form in the layout GA: two rooms side by side per band with no full-length passage (as in `src/spatial/reference_plans.py`), so 25–30 ft plots get passing layouts; the Phase 9 ablation shows the current side-spine form stops on most of them. Also revisit the A5 default (one attached + one common bath per floor), which over-fills small plots when the client gives no bath count.
- Vastu as an optional soft objective in the layout GA (moved out of the BTP scope on 2026-11-11). ResPlan has no orientation, so its rules would be hand-written.

These were in the original document-driven plan. They are out of the current scope but are genuine upgrades once the house pipeline works.

- **Organisational input: tender / SoW / DPR documents.** Re-enable the document path for public-works users: PyMuPDF for native PDFs, PaddleOCR + LayoutLMv3 for scanned or complex documents (code already in `src/ingestion/{ocr,layout}.py`, untested).
- **Legal-vs-construction content filter.** Classifier trained on manually tagged reference documents to strip contractual boilerplate (observed ~80% of real tenders).
- **Reference corpus of 15–20 real documents** as few-shot and evaluation set for document extraction accuracy.
- **Coreference / disambiguation pass** over long documents (e.g. "the structure" → the named building).
- **Live MCP co-pilot.** Expose a fixed set of PyRevit tool functions (create_wall, create_door, create_room, …) through an MCP server for interactive edits after the deterministic build.
- **Revit-free IFC export.** The IfcOpenShell builder from `strata_demo` as an open path when Revit isn't available.
- **Node.js / Express backend** connecting to the existing demo website, if a separate production frontend is built.
- **Higher BIM dimensions (4D–10D)** from the original proposal: schedule (4D), cost from CPWD DSR (5D), energy/sustainability (6D), facility management (7D), safety (8D), lean (9D), risk (10D).
