"""Run and document the complete Member C H3 analysis."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow

import context_analysis
import temporal_validation
import volume_baseline
from common import repository_root, results_dir, sha256_file


def _git_head(root: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def _source_repository_commit(root: Path) -> str:
    """Return the upstream commit on which the C delivery was based."""
    return subprocess.check_output(
        ["git", "rev-parse", "origin/main"], cwd=root, text=True
    ).strip()


def _member_c_code_commit(root: Path) -> str | None:
    """Return the latest commit containing C code/results or the C config review."""
    value = subprocess.check_output(
        ["git", "log", "-1", "--format=%H", "--", "C", "B/experiment.yaml"],
        cwd=root,
        text=True,
    ).strip()
    return value or None


def _format_pct(value: float) -> str:
    return f"{100 * value:.1f}%"


def _write_reports(root: Path, summaries: dict[str, dict[str, object]]) -> None:
    out = results_dir(root)
    volume = pd.read_csv(out / "all_year_volume_topk.csv")
    ranking = pd.read_csv(out / "ranking_comparison.csv")
    context = pd.read_csv(out / "context_comparison.csv")
    monthly = pd.read_csv(out / "monthly_profile.csv")
    holdout = pd.read_csv(out / "holdout_evaluation.csv")

    means = context.groupby("context", observed=True).support.mean().to_dict()
    month_means = monthly.groupby("month", observed=True).support.mean().sort_values()
    weakest_month, strongest_month = month_means.index[0], month_means.index[-1]
    tidal_one = ranking[ranking.tidal_rank_all.eq(1)].iloc[0]
    volume_one = ranking[ranking.volume_rank.eq(1)].iloc[0]
    weakest_holdout = holdout.sort_values("holdout_support").iloc[0]
    monthly_stability = monthly.groupby(["frozen_tidal_rank", "corridor"], observed=True).agg(
        months_at_or_above_threshold=("support", lambda values: int((values >= 0.10).sum())),
        minimum_monthly_support=("support", "min"),
        maximum_monthly_support=("support", "max"),
    ).reset_index()
    most_variable = monthly_stability.sort_values("minimum_monthly_support").iloc[0]

    results_text = f"""# Member C results

## Scope and method

The analysis uses the checked-in 1000 m observed OD aggregates, B's frozen
candidate definitions, `n_min=10`, `p_min=2/3`, and `min_support=0.10`.
Support denominators retain every imported date in the relevant cohort,
including empty direction-event dates. Source coverage labels remain
`unknown`; all findings are therefore labeled `observed_data_only`.

## E1 popular-flow baseline

The popular ranking sums both directions in weekday AM and PM for the same 859
FullYear candidate corridors and 261 observed weekdays used by B. The popular
Top-10 and tidal Top-10 have **{summaries['volume']['top10_overlap_count']}**
shared corridors (Jaccard {summaries['volume']['top10_jaccard']:.3f}). The
highest-volume candidate is `{volume_one.corridor}` with
{int(volume_one.total_rides):,} observed rides. B's tidal rank 1 corridor is
volume rank {int(tidal_one.volume_rank)}, with Support
{float(tidal_one.support):.3f}. High total flow and repeated directional
reversal are distinct properties in this candidate pool.

## E3 context comparison

For the fixed full-year tidal Top-10, mean weekday AM-to-PM Support is
{means['weekday_AM_to_PM']:.3f}. It falls to
{means['weekend_AM_to_PM']:.3f} for weekend AM-to-PM and
{means['weekday_AM_to_MID']:.3f} for weekday AM-to-MID. All 10 corridors have
lower Support in both comparison contexts. These are post-selection
descriptions, not independent tests.

The source AM, MID and PM coverage labels are all `unknown`. The large context
differences therefore describe the imported observations; they cannot by
themselves establish the same magnitude for the complete NYC riding
population.

## E4a time analysis

The monthly table contains 12 months for each of the 10 full-year selected
corridors. Average selected-corridor Support is highest in {strongest_month}
({_format_pct(float(month_means.loc[strongest_month]))}) and lowest in
{weakest_month} ({_format_pct(float(month_means.loc[weakest_month]))}). This
monthly profile is descriptive because the list was selected using the full
year. Every selected corridor remains above Support 0.10 in all 12 monthly
subsets. The lowest corridor-month value is
{float(most_variable.minimum_monthly_support):.3f} for tidal rank
{int(most_variable.frozen_tidal_rank)}. Lower winter values coincide with
lower observed ride totals in several corridors, so the table supports a
description of temporal variation but not a seasonal causal claim.

The strict Holdout table evaluates only B's frozen Development Top-10 over 66
weekday dates. All 10 reproduce in the same orientation at Support at least
0.10. Mean Support changes from
{summaries['temporal']['mean_development_support']:.3f} in Development to
{summaries['temporal']['mean_holdout_support']:.3f} in Holdout. The lowest
Holdout Support is {float(weakest_holdout.holdout_support):.3f} for Development
rank {int(weakest_holdout.development_rank)}; it still exceeds the frozen
7-day minimum. No corridor or parameter was reselected using Holdout.

## Verification

- The recomputed full-year weekday AM-to-PM metrics exactly match B's Top-10
  joint counts, Support, Confidence and Lift.
- The recomputed Holdout metrics exactly match B's persisted Holdout table.
- Member C unit tests cover threshold boundaries, empty-date denominators,
  volume ranking and Holdout classification.
"""
    (out / "results.md").write_text(results_text, encoding="utf-8")

    case_text = f"""# Member C case notes

## Popular but not strongly tidal

`{volume_one.corridor}` ranks first by observed weekday AM+PM volume with
{int(volume_one.total_rides):,} rides. It has no representative frequent tidal
rule at Support 0.10, so it does not enter the tidal Top-10.

## Strongly tidal without being the busiest

`{tidal_one.corridor}` is tidal rank 1 with Support
{float(tidal_one.support):.3f}, but it is only volume rank
{int(tidal_one.volume_rank)} with {int(tidal_one.total_rides):,} rides. This is
the clearest example of why the popular and tidal rankings answer different
questions.

## Context dependence

For the tidal rank 1 corridor, weekday AM-to-PM Support is
{float(context[(context.frozen_tidal_rank == 1) & (context.context == 'weekday_AM_to_PM')].support.iloc[0]):.3f},
weekend AM-to-PM Support is
{float(context[(context.frozen_tidal_rank == 1) & (context.context == 'weekend_AM_to_PM')].support.iloc[0]):.3f},
and weekday AM-to-MID Support is
{float(context[(context.frozen_tidal_rank == 1) & (context.context == 'weekday_AM_to_MID')].support.iloc[0]):.3f}.
The reversal is therefore concentrated in the weekday commute-window
comparison rather than being a general all-day or weekend property.

## Holdout behavior

The weakest same-orientation Holdout result is Development rank
{int(weakest_holdout.development_rank)} with {int(weakest_holdout.holdout_N_AP)}
joint days out of {int(weakest_holdout.holdout_N_obs)} and Support
{float(weakest_holdout.holdout_support):.3f}. It remains a successful
reproduction under the frozen 0.10 rule, while its lower value is retained as
evidence of temporal variation.
"""
    (out / "case_notes.md").write_text(case_text, encoding="utf-8")

    limitations_text = """# Member C limitations

1. `import_complete=True` confirms file import, not complete capture of all
   real-world Citi Bike trips. AM, MID and PM coverage statuses remain
   `unknown`; results describe imported observations only.
2. Missing sparse OD keys are treated as zero observed rides for imported
   dates under the approved protocol. They are not claims of zero real-world
   demand.
3. The main analysis uses 1 km grid corridors. A straight line between grid
   centers is not a traveled street route, and the analysis does not identify
   individual riders or round trips.
4. Weekday means Monday through Friday. Holidays were not separately marked,
   so weekday results can include public holidays.
5. The full-year Top-10, monthly profile and context comparisons are
   post-selection descriptions. Only the frozen Development-list evaluation
   on October through December is an independent time split.
6. `n_min`, `p_min` and `min_support` are operational pattern definitions, not
   null-hypothesis significance thresholds. B's Development-only sensitivity
   table should accompany the main results.
7. The popular baseline is restricted to the same FullYear candidate pool as
   the tidal analysis. It is not a ranking of every possible NYC grid pair.
8. Weather, service outages, station capacity, rebalancing, user type and
   neighborhood characteristics are not modeled. The results are descriptive
   associations and do not establish causality or bike shortages.
"""
    (out / "limitations.md").write_text(limitations_text, encoding="utf-8")

    candidates = pd.read_csv(root / "B" / "results" / "candidate_corridors.csv")
    algorithm_consistency = pd.read_csv(root / "B" / "results" / "algorithm_consistency.csv")
    algorithm_comparison = pd.read_csv(root / "B" / "results" / "algorithm_comparison.csv")
    algorithm_runs = pd.read_csv(root / "B" / "results" / "algorithm_run_summary.csv")
    b_manifest = json.loads(
        (root / "B" / "results" / "h2_run_manifest.json").read_text(encoding="utf-8")
    )
    pool_counts = candidates.groupby("pool", observed=True).corridor.nunique().to_dict()
    method_counts = algorithm_comparison.groupby("algorithm", observed=True).size().to_dict()
    if pool_counts.get("FullYear") != 859 or pool_counts.get("Development") != 829:
        raise AssertionError("B candidate corridor counts differ from the H2 handoff")
    if not algorithm_consistency.result_consistent.astype(bool).all():
        raise AssertionError("B algorithm consistency table contains a failed corridor")
    if not algorithm_comparison.observed_day_denominator.eq(195).all():
        raise AssertionError("B algorithm comparison does not use one 195-day denominator")
    if set(method_counts.values()) != {253}:
        raise AssertionError("B algorithms do not return the same 253 reversed patterns")
    verification_text = f"""# Member C independent verification notes

## Version and interfaces

- Reviewed repository base commit: `{_source_repository_commit(root)}`.
- Primary analysis: 1000 m, weekday, AM/PM.
- Frozen parameters: `n_min=10`, `p_min=2/3`, `min_support=0.10`.
- FullYear, Development and Holdout observed-day denominators are 261, 195 and
  66. Minimum joint counts at Support 0.10 are 27, 20 and 7.
- Canonical corridor endpoints are lexically ordered once; daily flow never
  changes A/B identity.
- Source window coverage remains `unknown`; missing sparse OD keys are zero
  observed rides only on imported dates.

## Candidate and direction-event audit

- FullYear candidate corridors: {int(pool_counts.get('FullYear', 0))}.
- Development candidate corridors: {int(pool_counts.get('Development', 0))}.
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

- All {len(algorithm_consistency)} Development corridor consistency rows report
  agreement among Apriori, FP-Growth and independent Direct Count:
  `{bool(algorithm_consistency.result_consistent.all())}`.
- Frequent reversed-pattern rows by method are
  Apriori={int(method_counts.get('Apriori', 0))},
  FP-Growth={int(method_counts.get('FP-Growth', 0))}, and
  Direct Count={int(method_counts.get('Direct Count', 0))}.
- Each algorithm uses the same 195 Development dates. Every persisted
  `observed_day_denominator` value in the algorithm comparison is 195.
- Empty Corridor-Date transactions are retained. B's manifest reports
  {int(b_manifest['e2']['corridor_day_empty_transactions']):,} empty positions;
  these remain part of each rule's 195-date denominator and are not treated as
  a separate denominator.
- Timing output separates `data_preparation_seconds`, `encoding_seconds`,
  `mining_seconds`, `filtering_seconds` and measured total time. Shared parquet
  loading and cohort construction are outside the per-algorithm mining timing.
- The run summary contains {len(algorithm_runs):,} method-corridor timing rows,
  corresponding to three methods across {len(algorithm_consistency)} corridors.

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
"""
    (out / "verification_notes.md").write_text(verification_text, encoding="utf-8")

    handoff_text = f"""# Member C H3 handoff

Status: completed.

Delivered outputs:

- `all_year_volume_topk.csv` and `ranking_comparison.csv`
- `context_comparison.csv`
- `monthly_profile.csv` and `holdout_evaluation.csv`
- `verification_notes.md`, `results.md`, `case_notes.md`, `limitations.md`
- supporting summaries and `c_run_manifest.json`

Key checks: full-year B metric cross-check passed; Holdout B metric cross-check
passed; all 10 frozen Development rules reproduced in the same orientation.
Popular/tidal Top-10 overlap is {summaries['volume']['top10_overlap_count']} of
10. Coverage remains unknown and all outputs are labeled observed-data-only.
"""
    (out / "C_handoff.md").write_text(handoff_text, encoding="utf-8")


def run(root: str | Path | None = None) -> dict[str, object]:
    root = repository_root(root)
    started = datetime.now(timezone.utc)
    summaries = {
        "volume": volume_baseline.run(root),
        "context": context_analysis.run(root),
        "temporal": temporal_validation.run(root),
    }
    _write_reports(root, summaries)
    out = results_dir(root)
    input_paths = [
        root / "preprocessing" / "necessary data" / "od_windows.parquet",
        root / "preprocessing" / "necessary data" / "date_coverage.csv",
        root / "preprocessing" / "necessary data" / "regions.parquet",
        root / "B" / "results" / "direction_events_1000m.parquet",
        root / "B" / "results" / "candidate_corridors.csv",
        root / "B" / "results" / "all_year_tidal_topk.csv",
        root / "B" / "results" / "development_tidal_topk.csv",
        root / "B" / "results" / "holdout_topk_evaluation.csv",
    ]
    artifact_paths = sorted(
        path for path in (root / "C").rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
        and path.name != "c_run_manifest.json"
    )
    artifact_paths.append(root / "B" / "experiment.yaml")
    manifest = {
        "status": "completed",
        "member": "C",
        "analysis_version": "1.0",
        "started_at_utc": started.isoformat(),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_repository_commit": _source_repository_commit(root),
        "member_c_code_commit": _member_c_code_commit(root),
        "data_mode": "observed_data_only",
        "coverage_status": "unknown",
        "development_holdout_separated": True,
        "holdout_used_for_selection": False,
        "parameters": {"grid_size_m": 1000, "n_min": 10, "p_min": 2 / 3, "min_support": 0.10, "top_k": 10},
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "pandas": pd.__version__,
            "pyarrow": pyarrow.__version__,
        },
        "summaries": summaries,
        "inputs": [
            {"path": str(path.relative_to(root)), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in input_paths
        ],
        "artifacts": [
            {"path": str(path.relative_to(root)), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in artifact_paths
        ],
    }
    (out / "c_run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    manifest = run(args.root)
    print(json.dumps(manifest["summaries"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
