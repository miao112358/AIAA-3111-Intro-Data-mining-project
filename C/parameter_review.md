# Member C parameter review

Review status: **approved for the observed-data analysis**.

- Primary grid: 1000 m.
- `n_min=10`, with Development-only sensitivity values 5 and 20.
- `p_min=2/3`, with Development-only sensitivity values 0.60 and 0.75.
- `min_support=0.10`; the denominator is every imported date in the relevant
  cohort, including dates with an empty direction-event transaction.
- `top_k=10`; one representative orientation is retained per undirected
  corridor. B's checked-in deterministic ranking is accepted for this run.
- Development is 2025-01-01 through 2025-09-30. Holdout is 2025-10-01
  through 2025-12-31 and may not select corridors, orientations or parameters.
- Holdout reproduction requires the frozen orientation to reach Support 0.10.
  An opposite orientation reaching the threshold is reported separately as a
  direction flip. A zero denominator is unestimable.

The source window coverage labels remain `unknown`. Approval therefore applies
to the explicitly labeled `observed_data_only` analysis. It does not convert
unknown source coverage into complete real-world collection coverage.

