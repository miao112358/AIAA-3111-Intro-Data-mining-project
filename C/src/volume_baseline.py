"""E1 volume baseline using B's full-year candidate corridor pool."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from common import (
    DATA_MODE, DENOMINATOR_TYPE, GRID_SIZE_M, load_coverage, repository_root,
    require_columns, results_dir,
)


def build_volume_ranking(events: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    """Return every FullYear candidate ranked by observed bidirectional volume."""
    required_events = {
        "date", "area_a", "area_b", "am_ab_count", "am_ba_count",
        "pm_ab_count", "pm_ba_count",
    }
    require_columns(events, required_events, "direction_events_1000m.parquet")
    full = candidates[candidates.pool.eq("FullYear")].copy()
    require_columns(full, {"corridor", "area_a", "area_b"}, "candidate_corridors.csv")
    if full.corridor.duplicated().any():
        raise ValueError("FullYear candidate corridor IDs are not unique")
    candidate_ids = set(full.corridor.astype(str))
    working = events.copy()
    working["corridor"] = working.area_a.astype(str) + "<->" + working.area_b.astype(str)
    working = working[working.corridor.isin(candidate_ids)].copy()
    if set(working.corridor) != candidate_ids:
        raise ValueError("Direction events do not cover the full FullYear candidate pool")
    if working.duplicated(["corridor", "date"]).any():
        raise ValueError("Direction events contain duplicate corridor/date rows")
    counts = working.groupby(["corridor", "area_a", "area_b"], as_index=False).agg(
        observed_day_denominator=("date", "nunique"),
        am_ab_rides=("am_ab_count", "sum"),
        am_ba_rides=("am_ba_count", "sum"),
        pm_ab_rides=("pm_ab_count", "sum"),
        pm_ba_rides=("pm_ba_count", "sum"),
    )
    counts["am_total_rides"] = counts.am_ab_rides + counts.am_ba_rides
    counts["pm_total_rides"] = counts.pm_ab_rides + counts.pm_ba_rides
    counts["total_rides"] = counts.am_total_rides + counts.pm_total_rides
    counts = counts.sort_values(["total_rides", "corridor"], ascending=[False, True]).reset_index(drop=True)
    counts.insert(0, "volume_rank", range(1, len(counts) + 1))
    counts["candidate_pool"] = "FullYear"
    counts["grid_size_m"] = GRID_SIZE_M
    counts["day_type"] = "weekday"
    counts["windows"] = "AM+PM"
    counts["denominator_type"] = DENOMINATOR_TYPE
    counts["coverage_status"] = "unknown"
    counts["data_mode"] = DATA_MODE
    return counts


def _all_tidal_ranking(rules: pd.DataFrame) -> pd.DataFrame:
    require_columns(
        rules,
        {"corridor", "area_a", "area_b", "antecedent", "consequent", "support",
         "joint_count", "confidence", "lift", "passes_min_support"},
        "tidal_rules.csv",
    )
    eligible = rules[rules.passes_min_support.astype(bool)].copy()
    eligible = eligible.sort_values(
        ["support", "joint_count", "area_a", "area_b", "antecedent", "consequent"],
        ascending=[False, False, True, True, True, True],
    ).drop_duplicates("corridor", keep="first").reset_index(drop=True)
    eligible["tidal_rank_all"] = range(1, len(eligible) + 1)
    return eligible


def _add_region_centers(root: Path, frame: pd.DataFrame) -> pd.DataFrame:
    regions = pd.read_parquet(root / "preprocessing" / "necessary data" / "regions.parquet")
    regions = regions[regions.grid_size_m.eq(GRID_SIZE_M)][["region_id", "center_lon", "center_lat"]]
    if regions.region_id.duplicated().any():
        raise ValueError("regions.parquet contains duplicate 1000m region IDs")
    a = regions.rename(columns={"region_id": "area_a", "center_lon": "area_a_lon", "center_lat": "area_a_lat"})
    b = regions.rename(columns={"region_id": "area_b", "center_lon": "area_b_lon", "center_lat": "area_b_lat"})
    merged = frame.merge(a, on="area_a", how="left", validate="many_to_one").merge(
        b, on="area_b", how="left", validate="many_to_one"
    )
    if merged[["area_a_lon", "area_a_lat", "area_b_lon", "area_b_lat"]].isna().any().any():
        raise ValueError("Volume ranking contains region IDs absent from regions.parquet")
    return merged


def run(root: str | Path | None = None) -> dict[str, object]:
    root = repository_root(root)
    out = results_dir(root)
    b_results = root / "B" / "results"
    events = pd.read_parquet(b_results / "direction_events_1000m.parquet")
    candidates = pd.read_csv(b_results / "candidate_corridors.csv")
    rules = pd.read_csv(b_results / "tidal_rules.csv")
    tidal_top = pd.read_csv(b_results / "all_year_tidal_topk.csv")
    coverage = load_coverage(root)
    ranking = build_volume_ranking(events, candidates)
    expected_days = int((coverage.import_complete & coverage.day_type.astype(str).str.lower().eq("weekday")).sum())
    if not ranking.observed_day_denominator.eq(expected_days).all():
        raise AssertionError("Volume candidate denominators do not match imported weekdays")
    ranking = _add_region_centers(root, ranking)
    volume_top = ranking.head(10).copy()
    tidal_all = _all_tidal_ranking(rules)
    tidal_top_ids = set(tidal_top.corridor.astype(str))
    volume_top_ids = set(volume_top.corridor.astype(str))
    union_ids = tidal_top_ids | volume_top_ids
    comparison = ranking[ranking.corridor.isin(union_ids)].copy()
    comparison = comparison.merge(
        tidal_all[["corridor", "tidal_rank_all", "support", "confidence", "lift", "joint_count", "antecedent", "consequent"]],
        on="corridor", how="left", validate="one_to_one",
    )
    comparison["in_tidal_top10"] = comparison.corridor.isin(tidal_top_ids)
    comparison["in_volume_top10"] = comparison.corridor.isin(volume_top_ids)
    comparison["in_both_top10"] = comparison.in_tidal_top10 & comparison.in_volume_top10
    overlap = len(tidal_top_ids & volume_top_ids)
    union = len(union_ids)
    comparison["top10_overlap_count"] = overlap
    comparison["top10_overlap_share_of_10"] = overlap / 10
    comparison["top10_jaccard"] = overlap / union if union else None
    comparison = comparison.sort_values(
        ["in_both_top10", "in_tidal_top10", "in_volume_top10", "tidal_rank_all", "volume_rank"],
        ascending=[False, False, False, True, True], na_position="last",
    ).reset_index(drop=True)
    volume_top.to_csv(out / "all_year_volume_topk.csv", index=False)
    comparison.to_csv(out / "ranking_comparison.csv", index=False)
    summary = {
        "full_year_candidate_corridors": int(len(ranking)),
        "observed_weekdays": expected_days,
        "volume_topk_rows": int(len(volume_top)),
        "tidal_topk_rows": int(len(tidal_top)),
        "top10_overlap_count": overlap,
        "top10_overlap_share_of_10": overlap / 10,
        "top10_jaccard": overlap / union if union else None,
        "highest_volume_corridor": str(volume_top.iloc[0].corridor),
        "highest_volume_total_rides": int(volume_top.iloc[0].total_rides),
    }
    (out / "ranking_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
