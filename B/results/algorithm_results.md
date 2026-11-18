# H2 Algorithm Comparison

Apriori and FP-Growth use the same Development date-keyed transactions for each Development-eligible corridor, min_support=0.10 and max_len=2. Direct Count enumerates singleton and pair counts independently from those transactions. Empty Development dates are retained. `algorithm_comparison.csv` contains only frequent same-corridor AM-to-PM reverse pairs; `algorithm_itemsets_all.csv` preserves the wider within-corridor itemset audit.

Consistency across candidate corridors: **PASS**. Checked corridor rows: 829. Each method has 253 frequent reversed patterns, for 759 method-pattern rows in `algorithm_comparison.csv`. The wider `algorithm_itemsets_all.csv` has 4,050 method-itemset rows. Frequent itemset/support counts and reversed-pattern counts match for Apriori, FP-Growth and Direct Count in all 829 corridors. See `algorithm_consistency.csv` and `algorithm_run_summary.csv` for per-Corridor agreement and timings.

Median per-corridor timings (seconds):

| Method | Median mining s | Median encoding s | Median filtering s | Median measured total s |
|---|---:|---:|---:|---:|
| Apriori | 0.000546 | 0.018577 | 0.000001 | 0.019149 |
| Direct Count | 0.000230 | 0.000000 | 0.000000 | 0.000246 |
| FP-Growth | 0.000556 | 0.018577 | 0.000001 | 0.019149 |

Parquet loading, aggregation, and cohort selection are shared data preparation outside per-algorithm timing; total pipeline runtime is recorded in `h2_run_manifest.json` and `h2_run.log`.
