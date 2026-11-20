# Phase 9 evaluation

Status 2026-11-20: arm C, the reference-plan comparison and the stage evaluations are final. Arms A and B
(10 briefs) are pending (free-tier Gemma was too slow; rerun on Groq). Every number below is produced by a
script in the repository; rerunning it reproduces the number.

## 1. Questions

1. Does STRATA deliver designs that are correct (code rules, structure) where an LLM alone does not?
   This is the ablation: A, B and C on the same briefs.
2. How often does it deliver a design at all, and what stops it when it doesn't?
3. Are its plans as good as a designer's? This is the comparison with hand-drawn reference plans.
4. How well does each LLM stage do its own job: the brief chatbot, and seeding the GA with LLM proposals?

## 2. Setup

**Briefs.** The 25 cases of `evaluation/brief_test_set.json`: plots from 20×50 to 45×64 ft, G+0 (with a future
floor) to G+2, single family, home with a shop and rental floors, a wheelchair user, parking, Vastu, a corner
plot and one city outside Guwahati (Shillong). Each case becomes a complete brief from its hand-written gold
facts, with the matrix defaults for whatever the client never gave (`src/evaluation/gold_briefs.py`). This
keeps the brief chatbot out of the comparison; it is scored on its own in §5.

**Arms** (`scripts/eval_ablation.py`, `src/evaluation/`):

| Arm | System | What it gets | What it returns |
|---|---|---|---|
| A | LLM writes the BIM model as code: the Text2BIM-style baseline on `strata_demo` (product owner, architect and coder agents) | the client's own words | an IfcOpenShell script, run to give an IFC |
| B | LLM + knowledge graph | the brief and every cited spec requirement (areas, widths, openings, stair, exits, setbacks) | a complete layout as schema-checked JSON (no GA, no checker feedback) |
| C | full STRATA | the brief | knowledge graph spec, GA layout, rule check, STAAD design loop, services, building JSON |

Arm A runs unchanged; only its LLM call is swapped so every arm uses the same model. Its IFC is read back
into a layout (`src/evaluation/ifc_layout.py`): rooms from IfcSpaces, doors and windows snapped to the nearest
room edge, so it isn't failed for drawing spaces to the inner wall faces.

**Scoring.** One scorer for all arms (`src/evaluation/ablation.py`); no arm sees it while designing:

- **Rule check:** the independent checker (`src/spatial/checker.py`), covering NBC 2016, the Guwahati bylaws
  and validity. It reports a plan as passing when no code or validity rule fails.
- **Circulation and programme:** every room reachable from the entrance through doors and stairs, and the
  rooms the brief asks for present.
- **Structure and output:**
  - STAAD.Pro design to IS 456 / IS 13920 through the same resize loop (arms B and C)
  - the building JSON validated with its services checks
  - for arm A, the baseline's own IFC checks
- **Cost:** time and LLM calls per design.

**Revit.** The Revit build is deterministic from a valid building JSON (Phase 7: 17/17 IFC checks on the
40×60 design). It is confirmed by hand on a subset, not per brief.

**Model.** Gemma 4 26B (`gemma-4-26b-a4b-it`) on the Gemini free tier, temperature 0. That is the brief
stage's model and the planned model for arms A and B. On the free tier one arm-A brief took over two hours
without finishing: 503s, and the coder reply needing more than 30 minutes. Arms A and B are therefore being
moved to Groq (`--provider groq`, gpt-oss-120b); the table names the model per arm.

## 3. Ablation results

`output/eval/ablation_table.md`, per brief `output/eval/ablation_cases.csv`.

### Arm C, all 25 briefs

| Metric | C: full STRATA |
|---|---|
| Design delivered | 4 / 25 (16%) |
| Stopped with brief changes that would fit | 20 / 25 (80%) |
| Refused: city has no bylaw layer (Shillong) | 1 / 25 (4%) |
| Delivered designs passing every code and validity rule | 4 / 4 |
| Delivered designs passing the STAAD design (IS 456 / IS 13920) | 4 / 4 |
| Delivered designs with a valid building JSON | 4 / 4 |
| Code checks passed, all plans incl. the GA's best on stopped briefs | 97% |
| Every room reachable (all plans) | 96% |
| Mean time per design | 114 s (delivered: 57–77 s) |
| LLM calls per design (layout to building JSON) | 0 |

**Findings**

- **No incorrect design delivered.** None of the 25 runs produced a design that breaks a code rule or fails
  STAAD. When the rules can't be met, STRATA stops and says what to change; for example,
  "one more storey and one bedroom fewer (fits)". Outside its bylaw coverage it refuses rather than guesses.
- **Coverage is the weakness.** The four delivered briefs are on plots 35 to 45 ft wide (b02, b10, b18, b21).
  The GA's reasons on the stopped briefs fall into two groups:
  - **Programme doesn't fit** ("N rooms did not fit"), on most of them. This is mainly on 25–30 ft plots, made
    worse by the A5 default when the client gives no bath count: one attached and one common bath per floor,
    so a G+1 house asks for four baths plus a dining room.
  - **Rooms just under the minimum** on the rest: baths 1.03 m wide against 1.2 m, bedrooms 6.6 m² against
    7.5 m², a living room 2.0 m wide against 2.4 m.
  - Both come from the GA's narrow-plot plan form (§4), not from the checker or the knowledge graph.

### Same 10 briefs, all arms

Pending. The 10 briefs are b01, b02, b03, b04, b07, b10, b11, b12, b21 and b22 (`LLM_SUBSET`). Arm C on these
10 delivers 3 (30%); every delivered design passes every rule and STAAD. Arms A and B will be filled in from
`python scripts/eval_ablation.py --arms A,B --provider groq`.

## 4. STRATA against hand-drawn reference plans

Optional item. There are no CPWD type designs for private plots in the reference documents, so the reference
is a designer's plan for the same brief (`src/spatial/reference_plans.py`, `src/spatial/sample_layout.py`;
`scripts/eval_reference.py` → `output/eval/reference_compare.md`).

| Plot | Plan | Passes every rule | Built-up m² | Circulation share of carpet | Mean habitable room m² |
|---|---|---|---|---|---|
| 30×40 G+1 | hand-drawn | yes | 75.3 | 31% | 10.4 |
| 30×40 G+1 | STRATA GA | no (living 2.0 m wide; stair room too narrow for two flights) | 70.8 | 45% | 8.2 |
| 30×50 G+1 | hand-drawn | yes | 91.7 | 25% | 10.5 |
| 30×50 G+1 | STRATA GA | no (bedroom 6.6 m²; bath 1.03 m wide) | 109.1 | 36% | 9.3 |
| 30×50 G+2 | hand-drawn | yes | 128.8 | 28% | 9.6 |
| 30×50 G+2 | STRATA GA | no (bedroom 6.6 m²; bath 1.03 m wide) | 163.7 | 37% | 9.0 |
| 40×60 G+1 | hand-drawn | yes | 148.8 | 26% | 12.4 |
| 40×60 G+1 | STRATA GA | yes | 137.4 | 25% | 11.3 |

On the 40×60 plot the GA matches the designer: both pass, circulation 25% against 26%, and room sizes within
10%. On 30-ft plots, with 5.54 m of buildable width, the designer's plans pass and the GA finds none.

Below 6.4 m of width the GA switches to a "side spine": a 1.35 m passage down one side and one room per band.
On these shallow envelopes the bands come out too shallow, and the passage takes 36–45% of the carpet area. The
designer instead puts two rooms side by side in each band, with no full-length passage. That form is
future work (`project_plan.md`).

## 5. Stage evaluations

**Brief chatbot** (`scripts/eval_briefs.py`, `output/eval/briefs_gemma-4-26b-a4b-it.json`; 25 cases):

| Measure | Result |
|---|---|
| Extraction recall / precision (opening message) | 0.91 / 0.99 |
| Wrong values / hallucinated values | 3 / 0 |
| Asks exactly for the missing Must items | 76% |
| Briefs complete and correct after the follow-ups | 20 / 25 |
| Mean LLM calls / time per conversation | 4.1 / 75 s |

**LLM proposals as GA seeds** (`scripts/eval_seeding.py`; 10 runs, population 40):

- Of the LLM's proposals, 1 of 4 is feasible on 40×60 and 0 of 4 on 30×50. The LLM alone misses minimum room
  sizes, the same failure arm B is expected to show at scale.
- Seeding doesn't improve the GA: best score at generation 40 is 3.99 seeded against 3.56 from random on 40×60,
  and 3.17 against 2.81 on 30×50; lower is better.
- Seeding does help at the start on the 30×50 brief (all 10 runs feasible at generation 0, against 8 from
  random), but random search catches up by generation 5.
- The orchestrator therefore runs the GA without LLM seeds.

**Robustness** (`scripts/robustness.py`; 14 varied briefs through the whole pipeline): no crashes.

- **Completed with every services check passing:** 7, including a 50×80 premium house, a G+2 joint family with
  a wheelchair user, weak soil and a future floor.
- **Stopped at the layout stage:** 7, all with suggested changes. These are the 30×40, 25×50 and 30×50 plots, a
  corner plot, a home with a shop, and the deliberately impossible 20×30 brief.

## 6. Limits of this evaluation

- **Gold briefs, not chatbot briefs.** The ablation measures the stages after the brief; the chatbot's 20/25
  multiplies into an end-to-end rate.
- **GA settings as in the app.** Arm C runs 8 restarts per brief from seed 1, as the web app does; more restarts could recover
  some near-misses such as the 1.18 m bath.
- **The checker is STRATA's own.** It is independent of the GA (separate code, reads only the layout), and its
  values come from the knowledge graph. The sign-off of `docs/kg_signoff.csv` is still pending.
- **Arm A's IFC reading is generous.** Openings are snapped to room edges and room types are taken from names,
  so arm A's scores are an upper bound.
- **Different model for arms A and B.** If they run on Groq, the brief stage (Gemma) and arms A/B use different
  models. The comparison between A, B and C stays fair, because all three take the same gold briefs.
