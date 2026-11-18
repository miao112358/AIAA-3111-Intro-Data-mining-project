# H2 Pattern Mining Method

## Data and observation coverage

This analysis uses the real NYC Citi Bike OD aggregates listed in
`h2_run_manifest.json`. `import_complete=True` identifies a date whose source
data was imported. It does not prove the source captured every ride that
occurred in NYC. The source AM/PM `coverage_status=unknown` is preserved and is
never changed to complete. Results are `observed_data_only`.

The date cohorts are 261 full-year weekdays, 195 Development weekdays, and 66
Holdout weekdays. A missing OD aggregation key for a successfully imported
date contributes zero observed rides for that Corridor/window, not zero
real-world rides. Explicit null counts, import failures, and duplicate source
aggregation keys are rejected. Empty date transactions are retained.

For Development there are 829 candidate Corridors and 195 date slots per
Corridor: `829 * 195 = 161,655` Corridor-Date positions. 94,086 positions have
no valid direction Item; 67,569 contain at least one. This empty-transaction
count is not a Support denominator. Every individual Development rule uses
`N=195`.

## Direction and rule definitions

Primary analysis is 1000m, weekdays, AM/PM. A/B are stable region IDs sorted
lexically once per undirected Corridor; daily flow never changes the
orientation. A candidate Corridor qualifies when both windows have at least
`n_min` observed rides and a valid dominant direction under `p_min` on at least
one observed date in that pool.

A reversed rule is `AM direction => opposite PM direction` on the same
Corridor. The complete rule table contains constructed directional rule
candidates, including zero-joint-count candidates; the frequent reversed-rule
count requires `Support >= min_support`. In this run, the complete candidates
and frequent subsets are:

- Full year: 1,165 constructed rule candidates; 244 meet `min_support=0.10`.
- Development: 1,082 constructed rule candidates; 253 meet
  `min_support=0.10`.

For joint count `J`, antecedent count `A`, consequent count `C`, and observed
date denominator `N`:

- `Support = J / N`
- `Confidence = J / A`
- `Lift = Confidence / (C / N)`

Counts and supporting dates are retained in the result tables. The minimum
integer joint support at 0.10 is 27 dates for full year (`N=261`) and 20 for
Development (`N=195`).

## Split and ranking rules

The Development candidate pool, E2 algorithm comparison, E4b sensitivity
analysis, and Development Top-10 use Development dates only. Holdout is used
only after the Development Top-10 rule definitions are frozen, to score those
rules independently on 66 dates. It does not select parameters, candidates,
directions, or the Development ranking.

The full-year candidate pool and full-year Top-10 use all 261 dates, including
the dates labeled Holdout. They are descriptive full-year results only and do
not affect Development selection or parameter choices. Top-10 requires
`Support >= 0.10`, allows one representative direction per undirected
Corridor, and ranks by descending Support, descending joint count, then
canonical Corridor IDs and rule labels for stable tie-breaking.

## Algorithm and scale scope

E2 compares Apriori, FP-Growth, and independent Direct Count on the same
Development date transactions for each of 829 candidate Corridors, with
`min_support=0.10` and `max_len=2`. Empty Corridor-Date transactions remain in
each 195-date denominator. E4b uses the five configurations in `experiment.yaml`
on Development only.

The 500m output is a real-data smoke test: the adapter checked the 500m source,
and a 5,000-row sample was rechecked for counts, orientation, and direction
decisions. No full 500m H2 or E4c experiment was run.
