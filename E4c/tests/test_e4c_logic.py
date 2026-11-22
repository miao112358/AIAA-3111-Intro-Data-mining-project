import sys
import unittest
from pathlib import Path

import pandas as pd
import pyarrow as pa

E4C_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(E4C_DIR))

from compare_topk import _make_comparison_tables
from e4c_common import aggregate_to_parents, analyze_scale


class E4cLogicTests(unittest.TestCase):
    def test_full_year_rule_uses_observed_day_denominator(self):
        dates = ["2025-01-06", "2025-01-07"]
        coverage = pd.DataFrame({
            "local_date": dates,
            "split": ["development", "development"],
            "day_type": ["weekday", "weekday"],
            "import_complete": [True, True],
            "AM_status": ["unknown", "unknown"],
            "PM_status": ["unknown", "unknown"],
        })
        daily = pd.DataFrame([
            {"date": day, "split": "development", "window": window, "area_a": "A", "area_b": "B", "ab_count": ab, "ba_count": ba}
            for day in dates
            for window, ab, ba in (("AM", 9, 1), ("PM", 1, 9))
        ])
        result = analyze_scale(daily, coverage, dates, 500, {
            "n_min": 5, "p_min": 2 / 3, "min_support": 1.0, "top_k": 10,
        })
        self.assertEqual(len(result["events"]), 2)
        self.assertEqual(len(result["rules"]), 1)
        self.assertEqual(result["rules"].iloc[0].support, 1.0)
        self.assertEqual(result["rules"].iloc[0].observed_day_denominator, 2)
        self.assertEqual(result["topk"].iloc[0]["rank"], 1)

    def test_parent_aggregation_preserves_direction_and_drops_internal_flow(self):
        table = pa.table({
            "local_date": ["2025-01-06"] * 3,
            "window": ["AM"] * 3,
            "origin_region": ["a1", "b1", "a1"],
            "destination_region": ["b1", "a1", "a2"],
            "ride_count": [5, 2, 11],
        })
        actual = aggregate_to_parents(table, {"a1": "A", "a2": "A", "b1": "B"})
        values = {
            (row.parent_origin, row.parent_destination): row.ride_count
            for row in actual.itertuples(index=False)
        }
        self.assertEqual(values, {("A", "B"): 5, ("B", "A"): 2})

    def test_topk_comparison_detects_direction_match_internal_and_mixed_children(self):
        top_500 = pd.DataFrame([
            {"corridor": "a1<->b1", "rank": 1, "antecedent": "AM:a1->b1", "consequent": "PM:b1->a1"},
            {"corridor": "a2<->b2", "rank": 2, "antecedent": "AM:b2->a2", "consequent": "PM:a2->b2"},
            {"corridor": "a1<->a2", "rank": 3, "antecedent": "AM:a1->a2", "consequent": "PM:a2->a1"},
        ])
        rules_1000 = pd.DataFrame([
            {"corridor": "A<->B", "area_a": "A", "area_b": "B", "antecedent": "AM:A->B", "consequent": "PM:B->A", "passes_min_support": True, "support": 0.5, "joint_count": 5},
        ])
        top_1000 = rules_1000.copy()
        top_1000["rank"] = 1
        tables = _make_comparison_tables(
            top_500, top_1000, rules_1000,
            {"a1": "A", "a2": "A", "b1": "B", "b2": "B"},
        )
        table1, table2, table3, table4 = tables[:4]
        self.assertEqual(table1.iloc[0].direction_match, "yes")
        self.assertEqual(table1.iloc[0].direction_500_to_1km, "A->B")
        self.assertEqual(table2.iloc[0].direction_500_to_1km, "A->B")
        self.assertEqual(table2["different_directions?"].unique().tolist(), ["yes"])
        self.assertEqual(table3.iloc[0].parent_grid, "A")
        self.assertEqual(len(table4), 0)


if __name__ == "__main__":
    unittest.main()
