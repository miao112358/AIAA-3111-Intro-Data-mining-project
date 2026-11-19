from __future__ import annotations

import unittest

import pandas as pd

from common import calculate_rule_metrics, classify_direction, holdout_classification
from volume_baseline import build_volume_ranking


class MemberCTests(unittest.TestCase):
    def test_direction_threshold_boundaries(self):
        self.assertEqual(classify_direction(7, 3), "A_TO_B")
        self.assertEqual(classify_direction(3, 7), "B_TO_A")
        self.assertIsNone(classify_direction(6, 4))
        self.assertIsNone(classify_direction(6, 3))
        self.assertIsNone(classify_direction(5, 5))

    def test_unified_metrics_keep_empty_dates_in_denominator(self):
        corridor = "A<->B"
        counts = pd.DataFrame([
            {"local_date": "2025-01-01", "corridor": corridor, "window": "AM", "ab_count": 7, "ba_count": 3},
            {"local_date": "2025-01-01", "corridor": corridor, "window": "PM", "ab_count": 3, "ba_count": 7},
            {"local_date": "2025-01-02", "corridor": corridor, "window": "AM", "ab_count": 8, "ba_count": 2},
            {"local_date": "2025-01-02", "corridor": corridor, "window": "PM", "ab_count": 8, "ba_count": 2},
        ])
        counts["local_date"] = pd.to_datetime(counts.local_date)
        dates = pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-03"]).tolist()
        result = calculate_rule_metrics(counts, dates, corridor, "A_TO_B")
        self.assertEqual(result["N_obs"], 3)
        self.assertEqual(result["N_A"], 2)
        self.assertEqual(result["N_P"], 1)
        self.assertEqual(result["N_AP"], 1)
        self.assertEqual(result["N_eligible"], 2)
        self.assertAlmostEqual(result["support"], 1 / 3)
        self.assertAlmostEqual(result["confidence"], 1 / 2)
        self.assertAlmostEqual(result["conditional_reversal_rate"], 1 / 2)
        self.assertEqual(result["total_rides"], 40)

    def test_holdout_classification(self):
        self.assertEqual(holdout_classification(0.10, 0.0, 66), "reproduced_same_orientation")
        self.assertEqual(holdout_classification(0.05, 0.20, 66), "direction_flipped")
        self.assertEqual(holdout_classification(0.05, 0.05, 66), "not_reproduced")
        self.assertEqual(holdout_classification(None, None, 0), "unestimable")

    def test_volume_ranking_uses_bidirectional_am_pm_counts(self):
        events = pd.DataFrame([
            {"date": "2025-01-01", "area_a": "A", "area_b": "B", "am_ab_count": 10,
             "am_ba_count": 1, "pm_ab_count": 2, "pm_ba_count": 3},
            {"date": "2025-01-02", "area_a": "A", "area_b": "B", "am_ab_count": 1,
             "am_ba_count": 1, "pm_ab_count": 1, "pm_ba_count": 1},
            {"date": "2025-01-01", "area_a": "A", "area_b": "C", "am_ab_count": 20,
             "am_ba_count": 0, "pm_ab_count": 0, "pm_ba_count": 0},
            {"date": "2025-01-02", "area_a": "A", "area_b": "C", "am_ab_count": 0,
             "am_ba_count": 0, "pm_ab_count": 0, "pm_ba_count": 0},
        ])
        candidates = pd.DataFrame([
            {"pool": "FullYear", "corridor": "A<->B", "area_a": "A", "area_b": "B"},
            {"pool": "FullYear", "corridor": "A<->C", "area_a": "A", "area_b": "C"},
        ])
        ranking = build_volume_ranking(events, candidates)
        self.assertEqual(ranking.corridor.tolist(), ["A<->B", "A<->C"])
        self.assertEqual(ranking.total_rides.tolist(), [20, 20])
        self.assertEqual(ranking.volume_rank.tolist(), [1, 2])


if __name__ == "__main__":
    unittest.main()
