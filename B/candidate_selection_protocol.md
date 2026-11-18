# H2 Candidate Selection Protocol

This protocol records the approved observed-day denominator used for the real
NYC Citi Bike H2 analysis. It supersedes the earlier draft that required known
window coverage. Parameters remain candidate parameters pending C review.

## Coverage and observed dates

- `import_complete=True` means the date's source data was imported. It does not
  establish that NYC captured every ride that occurred in reality.
- Keep the source AM/PM `coverage_status=unknown` unchanged. Unknown coverage is
  not an exclusion condition for this approved analysis and is never relabeled
  complete.
- The analysis cohort consists of imported weekday dates. A date with no OD
  aggregate for a corridor/window has zero **observed** rides for that
  corridor/window. This is not evidence of zero real-world rides.
- Explicit null OD values, failed imports, and duplicate source aggregation
  keys are errors and must not be converted to zero.
- The cohort sizes used by the current run are 261 full-year weekdays, 195
  Development weekdays, and 66 Holdout weekdays. Support denominators are
  separately 261, 195, and 66 for the corresponding analysis; empty
  transactions stay in the denominator.
- Every metric and result describes `observed_data_only` with
  `coverage_status=unknown`.

## Corridor eligibility

- Run one spatial scale at a time. The primary scale is 1000m; 500m is a
  separate smoke test, not a full H2 analysis.
- Corridor endpoints are stable mapped region IDs. Set A/B once by lexical
  ordering of the stable IDs; never reorient a corridor based on daily flow.
- A corridor qualifies when, on at least one observed weekday in that pool,
  both AM and PM have at least `n_min` observed rides and each window has a
  valid dominant direction under `p_min`.
- A rule candidate is AM direction => the opposite PM direction on that same
  corridor. The complete rule table retains generated candidates, including
  those with zero joint occurrences. A **frequent reversal rule** must also
  meet `min_support`.
- Missing OD keys are interpreted as zero observed rides only for successfully
  imported dates, per the approved data semantics. Source nulls, import
  failures, and duplicate keys remain errors.

## Separate candidate pools and Holdout boundary

- **Development pool:** eligibility is determined only from Development
  dates. It is the only pool used to establish the Development candidate list,
  run E2, run E4b, and produce the Development Top-10.
- Freeze the Development Top-10 before evaluating it on Holdout. Holdout
  contributes only to separate evaluation of those frozen rules; it does not
  choose parameters, Development candidates, or their representative
  directions.
- **Full-year pool and Top-10:** use all 261 weekdays, including the 66 dates
  assigned to Holdout. They are descriptive full-year results only and must
  not influence Development selection, parameter choices, or claims of
  independent Holdout validation.

## Rule metrics and ranking

For a rule with joint count `J`, antecedent count `A`, consequent count `C`,
and `N` observed dates:

- Support = `J / N`
- Confidence = `J / A`
- Lift = `Confidence / (C / N)`

Store `N`, `A`, `C`, `J`, and supporting dates alongside the ratios. In this
run, `min_support=0.10` requires at least 27 joint dates for the 261-day
full-year cohort and at least 20 for the 195-day Development cohort.

Top-k includes rules meeting minimum support and allows only one representative
rule per undirected corridor. Rank by descending Support, descending joint
count, then ascending canonical `(area_a, area_b)` and rule labels for stable
tie-breaking. The representative direction is the selected rule's AM
antecedent and opposite PM consequent.

## Current run reference

The real results and exact cohort counts are recorded in
`results/h2_run_manifest.json`; the output tables are under `results/`.
Candidate parameters are documented in `experiment.yaml` and remain subject to
C review. No result from the full-year descriptive pool is used to revise the
Development pool.
