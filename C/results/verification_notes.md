# Member C independent verification notes

## Version and interfaces

- Reviewed repository base commit: `702f2527a9f8adf9df980828841c5d75cc6f8f81`.
- Primary analysis: 1000 m, weekday, AM/PM.
- Frozen parameters: `n_min=10`, `p_min=2/3`, `min_support=0.10`.
- FullYear, Development and Holdout observed-day denominators are 261, 195 and
  66. Minimum joint counts at Support 0.10 are 27, 20 and 7.
- Canonical corridor endpoints are lexically ordered once; daily flow never
  changes A/B identity.
- Source window coverage remains `unknown`; missing sparse OD keys are zero
  observed rides only on imported dates.

## Candidate and direction-event audit

- FullYear candidate corridors: 859.
- Development candidate corridors: 829.
- C independently reconstructed weekday AM/PM direction events from the raw
  OD parquet for all 10 full-year selected corridors and all 10 frozen
  Development corridors.
- All full-year Top-10 joint counts, Support, Confidence and Lift match B's
  persisted table exactly.
- All frozen-rule Holdout antecedent, consequent and joint counts, Support,
  Confidence and Lift match B's persisted Holdout table exactly.
- Threshold-boundary tests confirm that 7:3 at total 10 passes, while 6:4,
  total below 10 and a tie do not form direction events.

## Rule and algorithm audit

- All 829 Development corridor consistency rows report
  agreement among Apriori, FP-Growth and independent Direct Count:
  `True`.
- Frequent reversed-pattern rows by method are
  Apriori=253,
  FP-Growth=253, and
  Direct Count=253.
- Each algorithm uses the same 195 Development dates. Every persisted
  `observed_day_denominator` value in the algorithm comparison is 195.
- Empty Corridor-Date transactions are retained. B's manifest reports
  94,086 empty positions;
  these remain part of each rule's 195-date denominator and are not treated as
  a separate denominator.
- Timing output separates `data_preparation_seconds`, `encoding_seconds`,
  `mining_seconds`, `filtering_seconds` and measured total time. Shared parquet
  loading and cohort construction are outside the per-algorithm mining timing.
- The run summary contains 2,487 method-corridor timing rows,
  corresponding to three methods across 829 corridors.

## Executed tests

- Member C tests: 4 of 4 passed. They cover direction thresholds, empty dates
  in denominators, bidirectional volume ranking and Holdout classification.
- B tests under the locally available compatibility environment: 71 of 72
  passed. The only failure asserts the unavailable future package version
  `mlxtend==0.25.0`; the installed compatible version reports `0.23.4`.
  No numerical or algorithm-consistency test failed.

## Verification conclusion

B's persisted candidate counts, selected-rule metrics, Holdout metrics and
three-method agreement are internally consistent with C's independent raw-data
recalculations. This verifies the observed-data computation, not completeness
of real-world Citi Bike collection.
