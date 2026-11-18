# Group 5 Pattern Mining — B Module

This repository contains B's reusable reversal and pattern-mining modules and
the real-data H2 outputs. The H2 primary analysis is 1000m weekday AM/PM;
500m is a smoke test only. Research outputs describe observed NYC Citi Bike
records and do not establish that every real-world ride was captured.

## Environment and tests

Install the pinned dependencies with:

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

The real-data adapter also requires `pyarrow` for Parquet I/O. The recorded H2
run used Python 3.14.6, pandas 3.1.1, mlxtend 0.25.0, and pyarrow 26.0.0; see
`results/h2_run_manifest.json` for the interpreter and environment details.

## Core module interfaces

1. `reversal_detection.get_main_direction(ab_count, ba_count, min_total_rides,
   min_dominance)` classifies a single corridor/window; `detect_reversal(...)`
   compares AM and PM directions.
2. `transaction_builder.build_transactions(od_windows, date_coverage,
   BuilderConfig(...))` returns a `BuildResult` with separate
   `development_transactions`, `holdout_transactions`, direction records and
   audit data. Generic input requires explicit coverage booleans and a
   configured missing-row policy.
3. `apriori_mining.mine_development(transactions, min_support, max_len, ...)`
   encodes date-keyed transactions and mines Development frequent itemsets.
4. `fpgrowth_mining.mine_fpgrowth(transactions, min_support, max_len, ...)`
   uses the same encoding and item metadata contract for FP-Growth.
5. `rule_evaluation.evaluate_development_rules(...)` calculates AM-to-PM
   reversed rule metrics; `evaluate_holdout_rules(...)` evaluates frozen
   Development rule definitions on Holdout only.
6. `algorithm_benchmark.benchmark_build_result(...)` benchmarks Apriori and
   FP-Growth using only `development_transactions`.

The current A data uses a different schema and `unknown` window coverage. The
real-data H2 path therefore uses `real_data_adapter.load_real_data(root,
grid_size_m)` and `run_h2_experiments.py`, without changing the generic
Transaction Builder's boolean-coverage contract. The H2 manifest records all
source file paths and cohort sizes.

## H2 statistical basis

- `import_complete=True` identifies an imported observation date. It is
  distinct from real-world collection completeness.
- Keep source `coverage_status=unknown`; do not relabel it complete.
- An absent corridor/window OD key is zero observed rides in the imported
  dataset, not a claim of zero real-world rides. Explicit nulls, failed imports
  and duplicate aggregation keys are errors.
- Use observed weekday denominators: 261 full-year, 195 Development, and 66
  Holdout. Empty transactions remain in Support's denominator.
- Development determines its candidate pool and Top-10. Holdout only evaluates
  those frozen Development rules. Full-year Top-10 includes Holdout and is
  descriptive only.

See `candidate_selection_protocol.md`, `experiment.yaml`, and
`results/pattern_method.md` for the approved selection and metric definitions.
`results/H2_completion_report.md` summarizes the actual H2 outputs, while
`results/B_handoff.md` is the short C/D handoff.

## Main H2 result files

- `direction_events_1000m.parquet`: candidate Corridor x full-year weekday
  event records with observed counts, directions, statuses and source coverage
  labels.
- `candidate_corridors.csv`: separate FullYear and Development candidate
  pools with qualification dates.
- `tidal_rules.csv`, `all_year_tidal_topk.csv`,
  `development_tidal_rules.csv`, `development_tidal_topk.csv`: count-backed
  E1 rules and ranked lists.
- `algorithm_comparison.csv`: frequent same-Corridor AM-to-PM reversed pairs
  for Apriori, FP-Growth and independent Direct Count.
- `algorithm_itemsets_all.csv`, `algorithm_consistency.csv`,
  `algorithm_run_summary.csv`: wider E2 itemset, consistency and timing audit.
- `threshold_sensitivity.csv`: five Development-only E4b configurations.
- `holdout_topk_evaluation.csv`: independent scores for frozen Development
  Top-10 rules.
- `500m_smoke_sample.csv`: 5,000-row real-data smoke sample; not a full 500m
  experiment.
- `h2_run_manifest.json`, `h2_run.log`: input paths, configuration, versions,
  cohort sizes and run audit.

The current run reports 829 Development corridors x 195 dates = 161,655
Corridor-Date positions. Of these, 94,086 are empty transactions and 67,569
contain at least one valid direction Item. The empty count is not a Support
denominator; each Development rule uses 195 dates.

Older generic demo exports also remain under `results/` (for example,
`algorithm_benchmark.csv`, `fpgrowth_frequent_itemsets.csv`, and
`association_rules_development.csv`). Their metadata marks them as simulated
or demo outputs. Do not use them as evidence for the real H2 findings; use the
H2 files listed above.

## Rerun the H2 pipeline

After installing pandas, mlxtend and pyarrow and placing the confirmed A input
files under `preprocessing/necessary data/`, run:

```sh
python src/run_h2_experiments.py
```

This command reruns real-data H2 and overwrites its result files; it is not
needed to inspect or consume the checked-in results. Do not use Holdout to
change parameters or the Development list.
