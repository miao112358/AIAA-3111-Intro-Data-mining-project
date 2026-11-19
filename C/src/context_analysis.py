"""E3 context comparisons for B's fixed full-year tidal Top-10."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from common import (
    calculate_rule_metrics, cohort_dates, direction_from_item, load_coverage,
    load_selected_counts, repository_root, results_dir, split_corridor,
)


CONTEXTS = (
    ("weekday_AM_to_PM", "weekday", "AM", "PM"),
    ("weekend_AM_to_PM", "weekend", "AM", "PM"),
    ("weekday_AM_to_MID", "weekday", "AM", "MID"),
)


def run(root: str | Path | None = None) -> dict[str, object]:
    root = repository_root(root)
    out = results_dir(root)
    top = pd.read_csv(root / "B" / "results" / "all_year_tidal_topk.csv")
    if len(top) != 10 or top.corridor.duplicated().any():
        raise ValueError("all_year_tidal_topk.csv must contain 10 unique corridors")
    coverage = load_coverage(root)
    counts = load_selected_counts(root, top.corridor, windows=("AM", "MID", "PM"))
    rows: list[dict[str, object]] = []
    for rule in top.sort_values("rank").itertuples(index=False):
        area_a, area_b = split_corridor(rule.corridor)
        frozen_direction = direction_from_item(rule.antecedent, area_a, area_b)
        for context, day_type, first_window, second_window in CONTEXTS:
            dates = cohort_dates(coverage, day_type=day_type)
            metrics = calculate_rule_metrics(
                counts, dates, rule.corridor, frozen_direction,
                first_window=first_window, second_window=second_window,
            )
            metrics.update({
                "context": context,
                "day_type": day_type,
                "frozen_tidal_rank": int(rule.rank),
                "selection_basis": "all_year_tidal_top10",
                "analysis_type": "post_selection_descriptive",
            })
            rows.append(metrics)
    frame = pd.DataFrame(rows)
    weekday_check = frame[frame.context.eq("weekday_AM_to_PM")].set_index("corridor")
    for rule in top.itertuples(index=False):
        ours = weekday_check.loc[rule.corridor]
        if int(ours.N_AP) != int(rule.joint_count):
            raise AssertionError(f"Full-year joint-count cross-check failed: {rule.corridor}")
        for field in ("support", "confidence", "lift"):
            if abs(float(ours[field]) - float(getattr(rule, field))) > 1e-12:
                raise AssertionError(f"Full-year {field} cross-check failed: {rule.corridor}")
    baseline = frame[frame.context.eq("weekday_AM_to_PM")][["corridor", "support", "confidence"]].rename(
        columns={"support": "baseline_support", "confidence": "baseline_confidence"}
    )
    frame = frame.merge(baseline, on="corridor", how="left", validate="many_to_one")
    frame["support_difference_pp_vs_weekday_AM_to_PM"] = (frame.support - frame.baseline_support) * 100
    frame["confidence_difference_pp_vs_weekday_AM_to_PM"] = (frame.confidence - frame.baseline_confidence) * 100
    ordered = [
        "frozen_tidal_rank", "corridor", "context", "analysis_type", "selection_basis",
        "day_type", "first_window", "second_window", "antecedent_direction",
        "consequent_direction", "N_obs", "N_A", "N_P", "N_AP", "N_eligible",
        "support", "confidence", "lift", "conditional_reversal_rate", "total_rides",
        "support_difference_pp_vs_weekday_AM_to_PM",
        "confidence_difference_pp_vs_weekday_AM_to_PM", "joint_dates", "n_min", "p_min",
        "denominator_type", "coverage_status", "data_mode",
    ]
    frame[ordered].sort_values(["frozen_tidal_rank", "context"]).to_csv(
        out / "context_comparison.csv", index=False
    )
    means = frame.groupby("context", observed=True).agg(
        mean_support=("support", "mean"),
        median_support=("support", "median"),
        total_joint_days=("N_AP", "sum"),
        total_rides=("total_rides", "sum"),
    ).reset_index()
    pivot = frame.pivot(index="corridor", columns="context", values="support")
    summary = {
        "corridors": int(top.corridor.nunique()),
        "rows": int(len(frame)),
        "context_means": means.to_dict("records"),
        "weekend_support_lower_corridors": int(
            (pivot["weekend_AM_to_PM"] < pivot["weekday_AM_to_PM"]).sum()
        ),
        "mid_support_lower_corridors": int(
            (pivot["weekday_AM_to_MID"] < pivot["weekday_AM_to_PM"]).sum()
        ),
        "b_full_year_topk_cross_check": "passed",
    }
    (out / "context_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
