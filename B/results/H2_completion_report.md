# H2 Completion Report

Run status: **completed**. Inputs are real source files; values are observed-data results, not population-wide ride claims.

## Statistical denominator and interpretation

The approved denominator is imported observed weekdays (`import_complete=True`).
The source AM/PM coverage statuses remain `unknown`; this status is not changed
to complete. Import completion does not prove that every ride occurring in NYC
was captured. An absent OD aggregation key means zero observed rides in the
imported data for that Corridor/window, not zero real-world rides. Explicit
nulls, failed imports, and duplicate aggregation keys are rejected.

Support uses 261 observed weekdays for full-year rules, 195 for Development,
and 66 for Holdout evaluation. The 829 Development Corridors x 195 days produce
161,655 Corridor-Date positions: 94,086 are empty transactions and 67,569 are
non-empty. **94,086 is the empty-transaction count, not the total transaction
count and not the Support denominator.**

## E1

1000m weekdays: 261 full-year observed days, 195 Development days, 66 Holdout days. Full-year candidate Corridors: 859; constructed reversed-rule candidates: 1,165, of which 244 meet `min_support=0.10`. Development candidate Corridors: 829; constructed reversed-rule candidates: 1,082, of which 253 meet `min_support=0.10`. The full-year candidate pool and Top-10 include Holdout dates and are descriptive only; they do not influence Development candidates, parameters, or ranking. Holdout evaluation separately scores only the frozen Development Top-10.

### Development Top-10

| Rank | Corridor | AM antecedent | PM consequent | Joint days | Support |
|---:|---|---|---|---:|---:|
| 1 | utm18n_o0_v1:1000:586:4510<->utm18n_o0_v1:1000:586:4512 | AM:utm18n_o0_v1:1000:586:4510->utm18n_o0_v1:1000:586:4512 | PM:utm18n_o0_v1:1000:586:4512->utm18n_o0_v1:1000:586:4510 | 187 | 0.958974358974359 |
| 2 | utm18n_o0_v1:1000:585:4509<->utm18n_o0_v1:1000:586:4512 | AM:utm18n_o0_v1:1000:585:4509->utm18n_o0_v1:1000:586:4512 | PM:utm18n_o0_v1:1000:586:4512->utm18n_o0_v1:1000:585:4509 | 181 | 0.9282051282051282 |
| 3 | utm18n_o0_v1:1000:586:4512<->utm18n_o0_v1:1000:588:4514 | AM:utm18n_o0_v1:1000:588:4514->utm18n_o0_v1:1000:586:4512 | PM:utm18n_o0_v1:1000:586:4512->utm18n_o0_v1:1000:588:4514 | 181 | 0.9282051282051282 |
| 4 | utm18n_o0_v1:1000:586:4512<->utm18n_o0_v1:1000:586:4515 | AM:utm18n_o0_v1:1000:586:4515->utm18n_o0_v1:1000:586:4512 | PM:utm18n_o0_v1:1000:586:4512->utm18n_o0_v1:1000:586:4515 | 179 | 0.9179487179487179 |
| 5 | utm18n_o0_v1:1000:584:4512<->utm18n_o0_v1:1000:586:4512 | AM:utm18n_o0_v1:1000:584:4512->utm18n_o0_v1:1000:586:4512 | PM:utm18n_o0_v1:1000:586:4512->utm18n_o0_v1:1000:584:4512 | 178 | 0.9128205128205128 |
| 6 | utm18n_o0_v1:1000:585:4514<->utm18n_o0_v1:1000:586:4512 | AM:utm18n_o0_v1:1000:585:4514->utm18n_o0_v1:1000:586:4512 | PM:utm18n_o0_v1:1000:586:4512->utm18n_o0_v1:1000:585:4514 | 177 | 0.9076923076923077 |
| 7 | utm18n_o0_v1:1000:586:4512<->utm18n_o0_v1:1000:586:4514 | AM:utm18n_o0_v1:1000:586:4514->utm18n_o0_v1:1000:586:4512 | PM:utm18n_o0_v1:1000:586:4512->utm18n_o0_v1:1000:586:4514 | 175 | 0.8974358974358975 |
| 8 | utm18n_o0_v1:1000:583:4507<->utm18n_o0_v1:1000:583:4508 | AM:utm18n_o0_v1:1000:583:4507->utm18n_o0_v1:1000:583:4508 | PM:utm18n_o0_v1:1000:583:4508->utm18n_o0_v1:1000:583:4507 | 168 | 0.8615384615384616 |
| 9 | utm18n_o0_v1:1000:583:4507<->utm18n_o0_v1:1000:585:4508 | AM:utm18n_o0_v1:1000:585:4508->utm18n_o0_v1:1000:583:4507 | PM:utm18n_o0_v1:1000:583:4507->utm18n_o0_v1:1000:585:4508 | 167 | 0.8564102564102564 |
| 10 | utm18n_o0_v1:1000:585:4508<->utm18n_o0_v1:1000:585:4510 | AM:utm18n_o0_v1:1000:585:4508->utm18n_o0_v1:1000:585:4510 | PM:utm18n_o0_v1:1000:585:4510->utm18n_o0_v1:1000:585:4508 | 165 | 0.8461538461538461 |

### Full-year descriptive Top-10

| Rank | Corridor | AM antecedent | PM consequent | Joint days | Support |
|---:|---|---|---|---:|---:|
| 1 | utm18n_o0_v1:1000:586:4510<->utm18n_o0_v1:1000:586:4512 | AM:utm18n_o0_v1:1000:586:4510->utm18n_o0_v1:1000:586:4512 | PM:utm18n_o0_v1:1000:586:4512->utm18n_o0_v1:1000:586:4510 | 245 | 0.9386973180076629 |
| 2 | utm18n_o0_v1:1000:584:4512<->utm18n_o0_v1:1000:586:4512 | AM:utm18n_o0_v1:1000:584:4512->utm18n_o0_v1:1000:586:4512 | PM:utm18n_o0_v1:1000:586:4512->utm18n_o0_v1:1000:584:4512 | 237 | 0.9080459770114943 |
| 3 | utm18n_o0_v1:1000:586:4512<->utm18n_o0_v1:1000:588:4514 | AM:utm18n_o0_v1:1000:588:4514->utm18n_o0_v1:1000:586:4512 | PM:utm18n_o0_v1:1000:586:4512->utm18n_o0_v1:1000:588:4514 | 236 | 0.9042145593869731 |
| 4 | utm18n_o0_v1:1000:585:4514<->utm18n_o0_v1:1000:586:4512 | AM:utm18n_o0_v1:1000:585:4514->utm18n_o0_v1:1000:586:4512 | PM:utm18n_o0_v1:1000:586:4512->utm18n_o0_v1:1000:585:4514 | 235 | 0.9003831417624522 |
| 5 | utm18n_o0_v1:1000:586:4512<->utm18n_o0_v1:1000:586:4515 | AM:utm18n_o0_v1:1000:586:4515->utm18n_o0_v1:1000:586:4512 | PM:utm18n_o0_v1:1000:586:4512->utm18n_o0_v1:1000:586:4515 | 234 | 0.896551724137931 |
| 6 | utm18n_o0_v1:1000:585:4509<->utm18n_o0_v1:1000:586:4512 | AM:utm18n_o0_v1:1000:585:4509->utm18n_o0_v1:1000:586:4512 | PM:utm18n_o0_v1:1000:586:4512->utm18n_o0_v1:1000:585:4509 | 228 | 0.8735632183908046 |
| 7 | utm18n_o0_v1:1000:583:4507<->utm18n_o0_v1:1000:583:4508 | AM:utm18n_o0_v1:1000:583:4507->utm18n_o0_v1:1000:583:4508 | PM:utm18n_o0_v1:1000:583:4508->utm18n_o0_v1:1000:583:4507 | 225 | 0.8620689655172413 |
| 8 | utm18n_o0_v1:1000:586:4512<->utm18n_o0_v1:1000:586:4514 | AM:utm18n_o0_v1:1000:586:4514->utm18n_o0_v1:1000:586:4512 | PM:utm18n_o0_v1:1000:586:4512->utm18n_o0_v1:1000:586:4514 | 224 | 0.8582375478927203 |
| 9 | utm18n_o0_v1:1000:585:4508<->utm18n_o0_v1:1000:585:4510 | AM:utm18n_o0_v1:1000:585:4508->utm18n_o0_v1:1000:585:4510 | PM:utm18n_o0_v1:1000:585:4510->utm18n_o0_v1:1000:585:4508 | 217 | 0.8314176245210728 |
| 10 | utm18n_o0_v1:1000:583:4507<->utm18n_o0_v1:1000:585:4508 | AM:utm18n_o0_v1:1000:585:4508->utm18n_o0_v1:1000:583:4507 | PM:utm18n_o0_v1:1000:583:4507->utm18n_o0_v1:1000:585:4508 | 216 | 0.8275862068965517 |

## E2

Status: completed; same 195 Development dates and `min_support=0.10`, `max_len=2` across methods; Apriori, FP-Growth and Direct Count agree across all 829 candidate Corridors. Each method has 253 frequent reversed patterns; `algorithm_comparison.csv` has 759 rows total (253 per method), while `algorithm_itemsets_all.csv` has 4,050 rows across all within-Corridor frequent itemsets. The broader consistency details and per-Corridor runtimes are in `algorithm_consistency.csv` and `algorithm_run_summary.csv`.

## E4b

Status: completed; configurations: 5.

| Experiment | n_min | p_min | Dev observed days | Eligible corridors | Rules (support ≥0.10) | Top-10 overlap | Jaccard | Runtime s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Main | 10 | 0.6667 | 195 | 829 | 253 | 10 | 1.000 | 1.214 |
| S1_n5 | 5 | 0.6667 | 195 | 1733 | 487 | 7 | 0.538 | 2.049 |
| S2_n20 | 20 | 0.6667 | 195 | 360 | 120 | 9 | 0.818 | 1.100 |
| S3_p060 | 10 | 0.6 | 195 | 927 | 395 | 8 | 0.667 | 1.702 |
| S4_p075 | 10 | 0.75 | 195 | 540 | 118 | 6 | 0.429 | 1.130 |

## 500m smoke test

Status: completed; observed weekdays: 261; 135,224 unique 500m Corridors in the filtered real data; 5,000 sample rows were rechecked, including 50 valid direction decisions. This is a smoke test only, not a full 500m experiment; the scale remains isolated from 1000m.

All metrics carry `denominator_type=observed_days` and `coverage_status=unknown`. See `h2_run_manifest.json` and `h2_run.log` for environment and input provenance.
