"""E4a monthly description and strict frozen-rule Holdout evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from common import (
    MIN_SUPPORT, calculate_rule_metrics, cohort_dates, direction_from_item,
    holdout_classification, load_coverage, load_selected_counts,
    minimum_support_count, opposite, repository_root, results_dir, split_corridor,
)


def _monthly_profile(top: pd.DataFrame, coverage: pd.DataFrame,
                     counts: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    annual_support = top.set_index("corridor").support.to_dict()
    for rule in top.sort_values("rank").itertuples(index=False):
        area_a, area_b = split_corridor(rule.corridor)
        frozen = direction_from_item(rule.antecedent, area_a, area_b)
        for month in range(1, 13):
            dates = cohort_dates(coverage, day_type="weekday", month=month)
            metrics = calculate_rule_metrics(counts, dates, rule.corridor, frozen)
            metrics.update({
                "frozen_tidal_rank": int(rule.rank),
                "month": f"2025-{month:02d}",
                "selection_basis": "all_year_tidal_top10",
                "analysis_type": "post_selection_monthly_description",
                "annual_support": float(annual_support[rule.corridor]),
                "support_difference_pp_vs_annual": (
                    (metrics["support"] - annual_support[rule.corridor]) * 100
                    if metrics["support"] is not None else None
                ),
            })
            rows.append(metrics)
    return pd.DataFrame(rows)


def _holdout_evaluation(dev_top: pd.DataFrame, b_holdout: pd.DataFrame,
                        coverage: pd.DataFrame, counts: pd.DataFrame) -> pd.DataFrame:
    holdout_dates = cohort_dates(
        coverage, day_type="weekday", start="2025-10-01", end="2025-12-31"
    )
    b_index = b_holdout.set_index("corridor")
    if set(dev_top.corridor) != set(b_holdout.corridor):
        raise ValueError("B Holdout evaluation does not match the frozen Development Top-10")
    rows: list[dict[str, object]] = []
    for rule in dev_top.sort_values("rank").itertuples(index=False):
        area_a, area_b = split_corridor(rule.corridor)
        frozen = direction_from_item(rule.antecedent, area_a, area_b)
        same = calculate_rule_metrics(counts, holdout_dates, rule.corridor, frozen)
        flipped = calculate_rule_metrics(counts, holdout_dates, rule.corridor, opposite(frozen))
        reference = b_index.loc[rule.corridor]
        exact_fields = {
            "N_obs": "observed_day_denominator",
            "N_A": "antecedent_count",
            "N_P": "consequent_count",
            "N_AP": "joint_count",
        }
        for ours, theirs in exact_fields.items():
            if int(same[ours]) != int(reference[theirs]):
                raise AssertionError(f"Holdout cross-check failed for {rule.corridor}: {ours}")
        for field in ("support", "confidence", "lift"):
            ours = same[field]
            theirs = reference[field]
            if pd.isna(theirs) and ours is None:
                continue
            if abs(float(ours) - float(theirs)) > 1e-12:
                raise AssertionError(f"Holdout cross-check failed for {rule.corridor}: {field}")
        result = {
            "development_rank": int(rule.rank),
            "corridor": rule.corridor,
            "frozen_antecedent": rule.antecedent,
            "frozen_consequent": rule.consequent,
            "frozen_antecedent_direction": frozen,
            "development_N_obs": int(rule.observed_day_denominator),
            "development_N_AP": int(rule.joint_count),
            "development_support": float(rule.support),
            "development_confidence": float(rule.confidence),
            "development_lift": float(rule.lift),
            "holdout_N_obs": int(same["N_obs"]),
            "holdout_N_A": int(same["N_A"]),
            "holdout_N_P": int(same["N_P"]),
            "holdout_N_AP": int(same["N_AP"]),
            "holdout_N_eligible": int(same["N_eligible"]),
            "holdout_support": same["support"],
            "holdout_confidence": same["confidence"],
            "holdout_lift": same["lift"],
            "holdout_conditional_reversal_rate": same["conditional_reversal_rate"],
            "holdout_total_rides": int(same["total_rides"]),
            "holdout_min_support_count": minimum_support_count(int(same["N_obs"])),
            "opposite_orientation_N_AP": int(flipped["N_AP"]),
            "opposite_orientation_support": flipped["support"],
            "support_difference_pp_holdout_minus_development": (
                (same["support"] - float(rule.support)) * 100
                if same["support"] is not None else None
            ),
            "reproduction_status": holdout_classification(
                same["support"], flipped["support"], int(same["N_obs"]), MIN_SUPPORT
            ),
            "selection_frozen_before_holdout": True,
            "holdout_used_for_selection": False,
            "evaluation_split": "Holdout",
            "denominator_type": same["denominator_type"],
            "coverage_status": same["coverage_status"],
            "data_mode": same["data_mode"],
        }
        rows.append(result)
    return pd.DataFrame(rows)


def run(root: str | Path | None = None) -> dict[str, object]:
    root = repository_root(root)
    out = results_dir(root)
    b_results = root / "B" / "results"
    all_year_top = pd.read_csv(b_results / "all_year_tidal_topk.csv")
    dev_top = pd.read_csv(b_results / "development_tidal_topk.csv")
    b_holdout = pd.read_csv(b_results / "holdout_topk_evaluation.csv")
    if len(all_year_top) != 10 or len(dev_top) != 10:
        raise ValueError("Both B Top-10 inputs must contain 10 rows")
    corridors = set(all_year_top.corridor) | set(dev_top.corridor)
    coverage = load_coverage(root)
    counts = load_selected_counts(root, corridors, windows=("AM", "PM"))
    monthly = _monthly_profile(all_year_top, coverage, counts)
    holdout = _holdout_evaluation(dev_top, b_holdout, coverage, counts)
    monthly_order = [
        "frozen_tidal_rank", "corridor", "month", "analysis_type", "selection_basis",
        "first_window", "second_window", "antecedent_direction", "consequent_direction",
        "N_obs", "N_A", "N_P", "N_AP", "N_eligible", "support", "confidence", "lift",
        "conditional_reversal_rate", "total_rides", "annual_support",
        "support_difference_pp_vs_annual", "joint_dates", "n_min", "p_min",
        "denominator_type", "coverage_status", "data_mode",
    ]
    monthly[monthly_order].sort_values(["frozen_tidal_rank", "month"]).to_csv(
        out / "monthly_profile.csv", index=False
    )
    holdout.sort_values("development_rank").to_csv(out / "holdout_evaluation.csv", index=False)
    status_counts = holdout.reproduction_status.value_counts().to_dict()
    summary = {
        "monthly_rows": int(len(monthly)),
        "monthly_corridors": int(monthly.corridor.nunique()),
        "holdout_rows": int(len(holdout)),
        "holdout_observed_weekdays": int(holdout.holdout_N_obs.iloc[0]),
        "holdout_min_support_count": int(holdout.holdout_min_support_count.iloc[0]),
        "reproduction_status_counts": {str(key): int(value) for key, value in status_counts.items()},
        "b_holdout_cross_check": "passed",
        "mean_holdout_support": float(holdout.holdout_support.mean()),
        "mean_development_support": float(holdout.development_support.mean()),
    }
    (out / "temporal_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
