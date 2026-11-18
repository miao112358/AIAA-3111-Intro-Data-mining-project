# B Module H2 Handoff for C/D

## Completed scope

B provides six reusable foundations: reversal detection, Transaction Builder,
Apriori, FP-Growth, Algorithm Benchmark, and Rule Evaluation. The generic APIs
are:

| Module | Main interface | Purpose |
|---|---|---|
| `reversal_detection.py` | `get_main_direction(...)`, `detect_reversal(...)` | Direction status and AM/PM reversal classification |
| `transaction_builder.py` | `build_transactions(...)` | Build split date transactions and direction/source audit records |
| `apriori_mining.py` | `mine_development(...)`, `run_apriori(...)` | Encode date-keyed transactions and mine Development itemsets |
| `fpgrowth_mining.py` | `mine_fpgrowth(...)`, `run_fpgrowth(...)` | FP-Growth using the same transaction encoding contract |
| `algorithm_benchmark.py` | `benchmark_build_result(...)` | Compare Apriori/FP-Growth on Development transactions |
| `rule_evaluation.py` | `evaluate_development_rules(...)`, `evaluate_holdout_rules(...)` | Score AM-to-PM reversed rules and evaluate frozen rules separately |

The current A parquet/coverage schema is adapted by
`src/real_data_adapter.py`; the reproducible H2 entry point is
`src/run_h2_experiments.py`. The generic Transaction Builder retains its
explicit Boolean coverage contract and was not changed to reinterpret A's
`unknown` coverage.

## H2 results

- **E1, 1000m:** 261 full-year, 195 Development, 66 Holdout observed weekdays;
  859 full-year and 829 Development candidate Corridors. The full-year table
  has 1,165 constructed reversed-rule candidates, 244 meeting Support 0.10.
  Development has 1,082 constructed candidates, 253 meeting Support 0.10.
  Both Top-10 lists and complete rules are delivered.
- **E2:** Apriori, FP-Growth, and independent Direct Count agree for all 829
  Development Corridors. Each returns 253 frequent reversed patterns;
  `algorithm_comparison.csv` has 759 method-pattern rows. The broader itemset
  audit has 4,050 rows.
- **E4b:** all five approved Development-only configurations are in
  `threshold_sensitivity.csv`; no Holdout data were used for sensitivity
  choices.
- **500m:** real data adapter and 5,000-row direction smoke test only. This is
  not a full 500m experiment.

## Statistical basis and Holdout boundary

All metrics use `denominator_type=observed_days` and preserve
`coverage_status=unknown`. `import_complete=True` means the date was imported;
it does not establish completeness of real-world collection. Missing OD keys
mean zero **observed** rides in the imported data, not zero rides in reality.
Explicit nulls, failed imports, and duplicate aggregation keys are rejected.

Development has 829 × 195 = 161,655 Corridor-Date positions: 94,086 are empty
and 67,569 are non-empty. 94,086 is neither total transactions nor the Support
denominator. Each Development rule uses 195 dates; full-year and Holdout rules
use 261 and 66, respectively.

Development candidates, parameters, E2, E4b, and the Development Top-10 use
Development only. Holdout scores only the frozen Development Top-10. The
full-year descriptive candidate pool/Top-10 includes the dates labeled
Holdout, but is not used to select Development candidates, parameters, or
rankings.

## Files for review and consumption

| File | Use |
|---|---|
| `direction_events_1000m.parquet` | Per candidate Corridor/date observed counts, statuses and direction Items |
| `candidate_corridors.csv` | Full-year and Development eligible Corridor pools and qualifying dates |
| `tidal_rules.csv` | Full-year rule candidates with counts, metrics and support dates |
| `all_year_tidal_topk.csv` | Descriptive full-year Top-10; includes Holdout dates |
| `development_tidal_rules.csv` | Development rule candidates with count-backed metrics and dates |
| `development_tidal_topk.csv` | Frozen Development Top-10; use this for downstream independent Holdout evaluation |
| `holdout_topk_evaluation.csv` | Scores only the frozen Development Top-10 on 66 Holdout dates |
| `algorithm_comparison.csv` | Reversed same-Corridor patterns by algorithm, counts, Support and timings |
| `algorithm_itemsets_all.csv` | Wider within-Corridor frequent itemset audit |
| `algorithm_consistency.csv`, `algorithm_run_summary.csv` | Per-Corridor method agreement and timing summaries |
| `threshold_sensitivity.csv` | Five E4b Development configurations, Top-10 overlap and rule support changes |
| `500m_smoke_sample.csv` | 5,000 real aggregated records checked at 500m; smoke test only |
| `h2_run_manifest.json`, `h2_run.log` | Input paths, run parameters, versions, cohort and run audit |

Rule CSV columns include `antecedent`, `consequent`, `joint_count`,
`antecedent_count`, `consequent_count`, `observed_day_denominator`, `support`,
`confidence`, `lift`, and date lists. Consumers must use the split-specific
denominator and must not merge Development and Holdout metrics.

## Recommended use by C/D

1. Read `results/pattern_method.md` and this handoff before interpreting any
   observed rule as a population-wide NYC travel pattern.
2. Use `development_tidal_topk.csv` for Development-selected rules and
   `holdout_topk_evaluation.csv` only for their separate Holdout assessment.
3. Use `algorithm_comparison.csv` to compare the three methods on reversed
   patterns; use `algorithm_itemsets_all.csv` for the wider itemset audit.
4. Read per-rule counts and `observed_day_denominator`; do not infer Support
   from the number of Corridor-Date positions across all Corridors.
5. Do not use the full-year Top-10 or Holdout outcomes to revise Development
   selections or parameters.

## Research limitations

Coverage remains `unknown`, so these results describe imported observations
only and cannot establish that all real-world NYC rides are represented.
Candidate parameters remain subject to C review. The full-year ranking is
descriptive and includes Holdout dates. The 500m scale has only a smoke test.

Legacy generic demo exports are also present under `results/` from earlier
module validation. Their metadata identifies simulated/demo data. Use the H2
files listed above for the real-data results; do not cite those legacy demo
exports as research findings.
