# Member C results

## Scope and method

The analysis uses the checked-in 1000 m observed OD aggregates, B's frozen
candidate definitions, `n_min=10`, `p_min=2/3`, and `min_support=0.10`.
Support denominators retain every imported date in the relevant cohort,
including empty direction-event dates. Source coverage labels remain
`unknown`; all findings are therefore labeled `observed_data_only`.

## E1 popular-flow baseline

The popular ranking sums both directions in weekday AM and PM for the same 859
FullYear candidate corridors and 261 observed weekdays used by B. The popular
Top-10 and tidal Top-10 have **0**
shared corridors (Jaccard 0.000). The
highest-volume candidate is `utm18n_o0_v1:1000:585:4508<->utm18n_o0_v1:1000:585:4509` with
91,391 observed rides. B's tidal rank 1 corridor is
volume rank 47, with Support
0.939. High total flow and repeated directional
reversal are distinct properties in this candidate pool.

## E3 context comparison

For the fixed full-year tidal Top-10, mean weekday AM-to-PM Support is
0.880. It falls to
0.026 for weekend AM-to-PM and
0.083 for weekday AM-to-MID. All 10 corridors have
lower Support in both comparison contexts. These are post-selection
descriptions, not independent tests.

The source AM, MID and PM coverage labels are all `unknown`. The large context
differences therefore describe the imported observations; they cannot by
themselves establish the same magnitude for the complete NYC riding
population.

## E4a time analysis

The monthly table contains 12 months for each of the 10 full-year selected
corridors. Average selected-corridor Support is highest in 2025-08
(96.2%) and lowest in
2025-12 (67.0%). This
monthly profile is descriptive because the list was selected using the full
year. Every selected corridor remains above Support 0.10 in all 12 monthly
subsets. The lowest corridor-month value is
0.478 for tidal rank
6. Lower winter values coincide with
lower observed ride totals in several corridors, so the table supports a
description of temporal variation but not a seasonal causal claim.

The strict Holdout table evaluates only B's frozen Development Top-10 over 66
weekday dates. All 10 reproduce in the same orientation at Support at least
0.10. Mean Support changes from
0.902 in Development to
0.817 in Holdout. The lowest
Holdout Support is 0.712 for Development
rank 2; it still exceeds the frozen
7-day minimum. No corridor or parameter was reselected using Holdout.

## Verification

- The recomputed full-year weekday AM-to-PM metrics exactly match B's Top-10
  joint counts, Support, Confidence and Lift.
- The recomputed Holdout metrics exactly match B's persisted Holdout table.
- Member C unit tests cover threshold boundaries, empty-date denominators,
  volume ranking and Holdout classification.
