from __future__ import annotations

import argparse

from e4c_common import E4C_DIR, load_scale, reconcile_tables, region_parent_map, resolve_data_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile cross-parent 500m counts against 1000m OD counts.")
    parser.add_argument("--data-dir", help="Preprocessing output directory; defaults to the latest complete run.")
    args = parser.parse_args()

    data_dir = resolve_data_dir(args.data_dir)
    _, _, table_500, regions, parent_map = load_scale(data_dir, 500)
    _, _, table_1000, _, _ = load_scale(data_dir, 1000)
    comparison = reconcile_tables(table_500, table_1000, parent_map or region_parent_map(regions), data_dir)
    output = E4C_DIR / "check.csv"
    mismatches = comparison.loc[~comparison.is_match].copy()
    mismatches.to_csv(output, index=False)
    matched = int(comparison.is_match.sum())
    mismatched = len(comparison) - matched
    print(f"Rows compared: {len(comparison)}; matched: {matched}; mismatched: {mismatched}; mismatch rows saved: {len(mismatches)}; output: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
