from __future__ import annotations

import argparse
import json

import pandas as pd

from e4c_common import (
    E4C_DIR,
    analyze_scale,
    daily_corridors,
    load_parameters,
    load_scale,
    mapped_direction,
    parse_corridor,
    rank_rules,
    reconcile_tables,
    region_parent_map,
    resolve_data_dir,
)


def _direction_from_rule(row) -> str:
    return str(row.antecedent).split(":", 1)[1]


def _make_comparison_tables(top_500: pd.DataFrame, top_1000: pd.DataFrame, rules_1000: pd.DataFrame, parent_map: dict[str, str]):
    top_1000_set = set(top_1000.corridor.astype(str)) if not top_1000.empty else set()
    representative = rules_1000[
        rules_1000.passes_min_support.astype(bool)
    ].copy() if not rules_1000.empty else rules_1000.copy()
    representative = rank_rules(representative, max(len(representative), 1))
    directions_1000 = {
        str(row.corridor): _direction_from_rule(row)
        for row in representative.itertuples(index=False)
    }

    rows1, rows2, rows3, rows4 = [], [], [], []
    external_children: dict[str, list[dict]] = {}
    for row in top_500.itertuples(index=False):
        origin, destination = parse_corridor(str(row.corridor))
        p_origin, p_destination = parent_map[origin], parent_map[destination]
        direction_500 = _direction_from_rule(row)
        if p_origin == p_destination:
            rows3.append({
                "top_k_rank": int(row.rank),
                "parent_grid": p_origin,
                "corridor_500m": row.corridor,
                "direction_500m_AM": direction_500,
            })
            continue

        p_left, p_right = sorted((p_origin, p_destination))
        parent_key = f"{p_left}<->{p_right}"
        parent_direction = mapped_direction(direction_500, parent_map)
        rule_direction = directions_1000.get(parent_key, "")
        rows1.append({
            "top_k_rank": int(row.rank),
            "corridor_500m": row.corridor,
            "parent_corridor_1km": parent_key,
            "direction_500m_AM": direction_500,
            "direction_500_to_1km": parent_direction,
            "direction_1km_AM": rule_direction,
            "direction_match": (
                "yes" if parent_direction == rule_direction else "no"
            ) if rule_direction else "",
        })
        if parent_key not in top_1000_set:
            rows4.append({
                "top_k_rank": int(row.rank),
                "corridor_500m": row.corridor,
                "parent_corridor_1km": parent_key,
                "1km_topk_status": "not_in_1km_topk",
            })
        external_children.setdefault(parent_key, []).append({
            "rank_500m": int(row.rank),
            "corridor_500m": row.corridor,
            "direction_500m_AM": direction_500,
            "mapped_direction_1km_orientation": parent_direction,
        })

    for parent_key, children in sorted(external_children.items()):
        parent_direction = directions_1000.get(parent_key, "")
        child_directions = {item["mapped_direction_1km_orientation"] for item in children}
        flag = "yes" if len(child_directions) > 1 else "no"
        for child in sorted(children, key=lambda item: item["rank_500m"]):
            rows2.append({
                "top_k_rank": child["rank_500m"],
                "parent_corridor_1km": parent_key,
                "direction_1km": parent_direction,
                "corridor_500m": child["corridor_500m"],
                "direction_500m_AM": child["direction_500m_AM"],
                "direction_500_to_1km": child["mapped_direction_1km_orientation"],
                "different_directions?": flag,
            })

    table1 = pd.DataFrame(rows1, columns=[
        "top_k_rank", "corridor_500m", "parent_corridor_1km", "direction_500m_AM",
        "direction_500_to_1km", "direction_1km_AM", "direction_match"
    ])
    table2 = pd.DataFrame(rows2, columns=[
        "top_k_rank", "parent_corridor_1km", "direction_1km", "corridor_500m",
        "direction_500m_AM", "direction_500_to_1km", "different_directions?"
    ])
    table3 = pd.DataFrame(rows3, columns=["top_k_rank", "parent_grid", "corridor_500m", "direction_500m_AM"])
    table4 = pd.DataFrame(rows4, columns=["top_k_rank", "corridor_500m", "parent_corridor_1km", "1km_topk_status"])
    return table1, table2, table3, table4, top_1000_set, external_children


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare 500m Top-k against a same-input 1000m baseline.")
    parser.add_argument("--data-dir", help="Preprocessing output directory; defaults to the latest complete run.")
    parser.add_argument("--parameters", help="JSON parameter file; defaults to E4c/parameters.json.")
    parser.add_argument("--tables", nargs="+", choices=["1", "2", "3", "4"], help="Only write the selected comparison tables.")
    args = parser.parse_args()

    data_dir = resolve_data_dir(args.data_dir)
    params = load_parameters(args.parameters)
    check_path = E4C_DIR / "check.csv"
    if not check_path.is_file():
        raise FileNotFoundError("Run check.py first so statistics.json can include parent-count reconciliation.")
    mismatch_rows = pd.read_csv(check_path)

    top_500_path = E4C_DIR / "500m_tidal_topk.csv"
    rules_500_path = E4C_DIR / "500m_tidal_rules.csv"
    if not top_500_path.is_file() or not rules_500_path.is_file():
        raise FileNotFoundError("Run mine_500m_patterns.py first")
    top_500 = pd.read_csv(top_500_path)
    rules_500 = pd.read_csv(rules_500_path)
    coverage_500, _, raw_500, regions, parent_map = load_scale(data_dir, 500)
    coverage_1000, dates_1000, raw_1000, regions_1000, _ = load_scale(data_dir, 1000)
    result_1000 = analyze_scale(daily_corridors(raw_1000, 1000), coverage_1000, dates_1000, 1000, params)
    regions = regions_1000
    parent_map = parent_map or region_parent_map(regions)
    reconciliation = reconcile_tables(raw_500, raw_1000, parent_map, data_dir)
    expected_mismatches = reconciliation.loc[~reconciliation.is_match]
    if len(mismatch_rows) != len(expected_mismatches):
        raise ValueError("check.csv does not match reconciliation results for this input")
    if not mismatch_rows.empty and set(mismatch_rows.input_data_dir.astype(str)) != {str(data_dir)}:
        raise ValueError("check.csv was generated from a different data directory")
    table1, table2, table3, table4, top_1000_set, external_children = _make_comparison_tables(
        top_500, result_1000["topk"], result_1000["rules"], parent_map
    )
    outputs = {
        "1.csv": table1,
        "2.csv": table2,
        "3.csv": table3,
        "4.csv": table4,
    }
    selected_tables = set(args.tables) if args.tables else set(outputs)
    for number in selected_tables:
        outputs[f"{number}.csv"].to_csv(E4C_DIR / f"{number}.csv", index=False)
    if args.tables:
        print(f"Updated comparison tables: {', '.join(f'{number}.csv' for number in sorted(selected_tables))}")
        return 0

    mapped_top_corridors = set(external_children)
    top_500_internal = len(table3)
    overlap = len(mapped_top_corridors & top_1000_set)
    union = len(mapped_top_corridors | top_1000_set)
    comparable = len(table1)
    direction_matches = int(table1.direction_match.eq("yes").sum()) if comparable else 0
    ranks_1000 = {str(row.corridor): int(row.rank) for row in result_1000["topk"].itertuples(index=False)}
    rank_deltas = [
        rank_500 - ranks_1000[parent]
        for rank_500, (parent, _) in _top500_parent_map(top_500, parent_map).items()
        if parent in ranks_1000
    ]
    mismatched = int(len(expected_mismatches))
    events_500 = pd.read_parquet(E4C_DIR / "500m_direction_events.parquet", columns=["corridor"])
    statistics = {
        "data_dir": str(data_dir),
        "parameter_status": params.get("parameter_status", "unspecified"),
        "parameters": {key: params[key] for key in ("n_min", "p_min", "min_support", "top_k")},
        "cohort": {"day_type": "weekday", "windows": ["AM", "PM"], "observed_days": len(dates_1000), "denominator_type": "observed_days", "coverage_status": "unknown"},
        "baseline_1km": "recomputed in memory from the same local OD input used by 500m",
        "candidate_corridors": {
            "500m": int(events_500.corridor.nunique()),
            "1000m": len(result_1000["candidate_corridors"]),
        },
        "reversal_rules": {
            "500m_candidates": int(len(rules_500)),
            "500m_passing_min_support": int(rules_500.passes_min_support.astype(bool).sum()) if not rules_500.empty else 0,
            "1000m_candidates": int(len(result_1000["rules"])),
            "1000m_passing_min_support": int(result_1000["rules"].passes_min_support.sum()) if not result_1000["rules"].empty else 0,
        },
        "top_k": {
            "500m_rows": int(len(top_500)),
            "1000m_rows": int(len(result_1000["topk"])),
            "500m_internal_parent_corridors": top_500_internal,
            "mapped_parent_corridor_overlap_count": overlap,
            "mapped_parent_corridor_jaccard": overlap / union if union else None,
            "direction_comparable_rows": comparable,
            "direction_match_count": direction_matches,
            "direction_match_rate": direction_matches / comparable if comparable else None,
            "rank_change_comparable_rows": len(rank_deltas),
            "mean_absolute_rank_change": sum(abs(value) for value in rank_deltas) / len(rank_deltas) if rank_deltas else None,
            "rank_changes_500m_minus_1000m": rank_deltas,
            "parent_corridors_with_mixed_500m_directions": sum(
                len({child["mapped_direction_1km_orientation"] for child in children}) > 1
                for children in external_children.values()
            ),
            "500m_topk_parent_not_in_1000m_topk": len(table4),
        },
        "parent_count_reconciliation": {
            "compared_rows": int(len(reconciliation)),
            "matched_rows": int(reconciliation.is_match.sum()),
            "mismatched_rows": mismatched,
            "total_absolute_count_difference": int(reconciliation.difference_500m_minus_1000m.abs().sum()),
        },
        "outputs": list(outputs) + ["statistics.json"],
    }
    (E4C_DIR / "statistics.json").write_text(json.dumps(statistics, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(statistics, indent=2, ensure_ascii=False))
    return 0


def _top500_parent_map(top_500: pd.DataFrame, parent_map: dict[str, str]):
    result = {}
    for row in top_500.itertuples(index=False):
        origin, destination = parse_corridor(str(row.corridor))
        p_origin, p_destination = parent_map[origin], parent_map[destination]
        if p_origin != p_destination:
            parent = "<->".join(sorted((p_origin, p_destination)))
            result[int(row.rank)] = (parent, mapped_direction(_direction_from_rule(row), parent_map))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
