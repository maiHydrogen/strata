# Knowledge-graph sign-off (2026-11-19)

159 requirements. Quote found in the source text: 109; found with OCR differences: 33; cited page confirmed: 123. Statuses: verified 0, transcribed 135, assumed 24.

To sign off, read each clause in the code, put your name in `verified_by` (and the date in `verified_on`) in `docs/kg_signoff.csv`, then run `python scripts/kg_signoff.py --apply docs/kg_signoff.csv`. Assumed values stay assumed until their clause is found.

## Rows to look at first

| id | clause | page | quote | page check | checked by eye |
|---|---|---|---|---|---|
| fact_guwahati_population | Guwahati (M Corp.), total population | - | no source file in data/reference_docs | no source file in data/reference_docs |  |
| fact_guwahati_wind | Vol 1, Part 6 Section 1, Annex C (cl. 4.4.2) | 105 | found (100%) | CHECK: near pages [0, 1, 2, 3, 4, 5] | page 105 confirmed on the PDF (vol 1, PDF page 522); the OCR match falls on the Part 6 contents list |
| fact_guwahati_z | Vol 1, Part 6 Section 1, Annex K (cl. 5.3.4.2) | 120 | found (100%) | CHECK: near pages [0, 1, 2, 3, 4, 5] | page corrected 121 -> 120 (vol 1, PDF page 537) |
| fact_guwahati_zone | Vol 1, Part 6 Section 1, Annex K (cl. 5.3.4.2) | 120 | found (100%) | CHECK: near pages [0, 1, 2, 3, 4, 5] | page corrected 121 -> 120: Annex K column 1 (A to J) is on p. 120 (vol 1, PDF page 537) |
| req_beam_bar | cl. 6.2.1 (a) and 6.2.2 | 5 | NOT FOUND (0%) | - | read from the page image (IS 13920 p. 5, PDF page 7) |
| req_beam_cover | cl. 26.4.2, Table 16 | 47 | NOT FOUND (41%) | page found | IS 456 OCR runs words together here; text and printed page read on the PDF page (printed + 13) |
| req_beam_span_depth_cantilever | cl. 23.2.1 (a) | 37 | NOT FOUND (20%) | page found | IS 456 OCR runs words together here; text and printed page read on the PDF page (printed + 13) |
| req_beam_span_depth_continuous | cl. 23.2.1 (a) | 37 | NOT FOUND (20%) | page found | IS 456 OCR runs words together here; text and printed page read on the PDF page (printed + 13) |
| req_beam_span_depth_simple | cl. 23.2.1 (a) | 37 | NOT FOUND (20%) | page found | IS 456 OCR runs words together here; text and printed page read on the PDF page (printed + 13) |
| req_beam_steel_max | cl. 6.2.1 (a) and 6.2.2 | 5 | NOT FOUND (0%) | - | read from the page image (IS 13920 p. 5, PDF page 7) |
| req_beam_width | cl. 6.1.1 and 6.1.2 | 4 | NOT FOUND (0%) | - | read from the page image (IS 13920 p. 4, PDF page 6) |
| req_beam_width_depth | cl. 6.1.1 and 6.1.2 | 4 | NOT FOUND (0%) | - | read from the page image (IS 13920 p. 4, PDF page 6) |
| req_column_width | cl. 7.1.1 | 7 | NOT FOUND (2%) | page found | read from the page image (IS 13920 p. 7, PDF page 9) |
| req_exit_door_height | Vol 1, Part 4, cl. 4.4.2.4.1 (b) | 32 | found (91%) | page found | page 32 confirmed on the PDF (vol 1, PDF page 287) |
| req_exit_door_width | Vol 1, Part 4, cl. 4.4.2.4.1 (b) | 32 | found (91%) | page found | page 32 confirmed on the PDF (vol 1, PDF page 287); the OCR also has a similar sentence on p. 27 |
| req_far_base_3_6to4_5 | Bye-law 26(a), FAR table | 9730 | found (86%) | CHECK: near pages [1, 2, 3, 4, 5, 6] | the 2022 table at gazette p. 9730 (OCR page p026) has garbled numbers, so the match fell on the 2024 amendment's reprint |
| req_far_base_4_5to6_6 | Bye-law 26(a), FAR table | 9730 | found (86%) | CHECK: near pages [1, 2, 3, 4, 5, 6] | the 2022 table at gazette p. 9730 (OCR page p026) has garbled numbers, so the match fell on the 2024 amendment's reprint |
| req_far_base_6_6to8 | Bye-law 26(a), FAR table | 9730 | found (86%) | CHECK: near pages [1, 2, 3, 4, 5, 6] | the 2022 table at gazette p. 9730 (OCR page p026) has garbled numbers, so the match fell on the 2024 amendment's reprint |
| req_far_base_8to15 | Bye-law 26(a), FAR table | 9730 | found (86%) | CHECK: near pages [1, 2, 3, 4, 5, 6] | the 2022 table at gazette p. 9730 (OCR page p026) has garbled numbers, so the match fell on the 2024 amendment's reprint |
| req_far_base_over15 | Bye-law 26(a), FAR table | 9730 | found (86%) | CHECK: near pages [1, 2, 3, 4, 5, 6] | the 2022 table at gazette p. 9730 (OCR page p026) has garbled numbers, so the match fell on the 2024 amendment's reprint |
| req_far_max_3_6to4_5_plot1338 | Bye-law 26(a), FAR table | 9730 | found (86%) | CHECK: near pages [1, 2, 3, 4, 5, 6] | the 2022 table at gazette p. 9730 (OCR page p026) has garbled numbers, so the match fell on the 2024 amendment's reprint |
| req_far_max_3_6to4_5_plot670 | Bye-law 26(a), FAR table | 9730 | found (86%) | CHECK: near pages [1, 2, 3, 4, 5, 6] | the 2022 table at gazette p. 9730 (OCR page p026) has garbled numbers, so the match fell on the 2024 amendment's reprint |
| req_far_max_4_5to6_6_plot1338 | Bye-law 26(a), FAR table | 9730 | found (86%) | CHECK: near pages [1, 2, 3, 4, 5, 6] | the 2022 table at gazette p. 9730 (OCR page p026) has garbled numbers, so the match fell on the 2024 amendment's reprint |
| req_far_max_4_5to6_6_plot670 | Bye-law 26(a), FAR table | 9730 | found (86%) | CHECK: near pages [1, 2, 3, 4, 5, 6] | the 2022 table at gazette p. 9730 (OCR page p026) has garbled numbers, so the match fell on the 2024 amendment's reprint |
| req_far_max_6_6to8_plot1338 | Bye-law 26(a), FAR table | 9730 | found (86%) | CHECK: near pages [1, 2, 3, 4, 5, 6] | the 2022 table at gazette p. 9730 (OCR page p026) has garbled numbers, so the match fell on the 2024 amendment's reprint |
| req_far_max_6_6to8_plot670 | Bye-law 26(a), FAR table | 9730 | found (86%) | CHECK: near pages [1, 2, 3, 4, 5, 6] | the 2022 table at gazette p. 9730 (OCR page p026) has garbled numbers, so the match fell on the 2024 amendment's reprint |
| req_far_max_8to15_plot1338 | Bye-law 26(a), FAR table | 9730 | found (86%) | CHECK: near pages [1, 2, 3, 4, 5, 6] | the 2022 table at gazette p. 9730 (OCR page p026) has garbled numbers, so the match fell on the 2024 amendment's reprint |
| req_far_max_8to15_plot670 | Bye-law 26(a), FAR table | 9730 | found (86%) | CHECK: near pages [1, 2, 3, 4, 5, 6] | the 2022 table at gazette p. 9730 (OCR page p026) has garbled numbers, so the match fell on the 2024 amendment's reprint |
| req_far_max_over15_plot1338 | Bye-law 26(a), FAR table | 9730 | found (86%) | CHECK: near pages [1, 2, 3, 4, 5, 6] | the 2022 table at gazette p. 9730 (OCR page p026) has garbled numbers, so the match fell on the 2024 amendment's reprint |
| req_far_max_over15_plot670 | Bye-law 26(a), FAR table | 9730 | found (86%) | CHECK: near pages [1, 2, 3, 4, 5, 6] | the 2022 table at gazette p. 9730 (OCR page p026) has garbled numbers, so the match fell on the 2024 amendment's reprint |
| req_parking_2w_upto66 | Appendix-I, Sl. 1 Residential Buildings | 9798 | NOT FOUND (22%) | page found | Appendix-I table: OCR splits the cells, page 9798 confirmed |
| req_parking_car_120 | Appendix-I, Sl. 1 Residential Buildings | 9798 | NOT FOUND (22%) | page found | Appendix-I table: OCR splits the cells, page 9798 confirmed |
| req_parking_car_66to120 | Appendix-I, Sl. 1 Residential Buildings | 9798 | NOT FOUND (22%) | page found | Appendix-I table: OCR splits the cells, page 9798 confirmed |
| req_rc_concrete_grade | cl. 6.1.2 (p. 15), Table 5 | 20 | NOT FOUND (43%) | page found | IS 456 Table 5 is a table the OCR garbles; printed p. 15 = PDF page 20 |
| req_reinforcement_elongation | cl. 5.3.1 (as substituted by Amendment 1, 2017) | 3 | found (100%) | page found | IS 13920 cl. 5.3.1 read from the page image |
| req_reinforcement_proof_stress | cl. 5.3.1 (as substituted by Amendment 1, 2017) | 3 | found (100%) | page found | IS 13920 cl. 5.3.1 read from the page image |
| req_slab_cover | cl. 26.4.2, Table 16 | 47 | NOT FOUND (41%) | page found | IS 456 OCR runs words together here; text and printed page read on the PDF page (printed + 13) |
| req_slab_span_depth | cl. 24.1, NOTE 2 | 39 | NOT FOUND (8%) | page found | IS 456 OCR runs words together here; text and printed page read on the PDF page (printed + 13) |
| req_stair_tread | Vol 1, Part 3, cl. 12.18.1.2 | 49 | found (100%) | adjacent page (OCR puts page numbers out of order) | page 49 confirmed (vol 1, PDF page 154) |
| req_stair_width | Vol 1, Part 3, cl. 12.18.1.1(a) | 49 | found (100%) | adjacent page (OCR puts page numbers out of order) | page 49 confirmed: 12.18.1 ends p. 48, 12.18.1.1 starts p. 49 (vol 1, PDF pages 153-154) |
| req_strong_column | cl. 7.2.1 and 7.2.1.3 | 8 | NOT FOUND (4%) | page found | read from the page image (IS 13920 p. 8, PDF page 10) |
