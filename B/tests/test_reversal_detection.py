"""Unit tests for reversal detection using simulated corridor counts."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reversal_detection import (  # noqa: E402
    Direction, DirectionStatus, ReversalStatus, detect_reversal, get_main_direction,
)


class GetMainDirectionTests(unittest.TestCase):
    def test_am_ab_pm_ba_reversal(self):
        result = detect_reversal(18, 6, 5, 15)
        self.assertEqual(result.reversal_status, ReversalStatus.REVERSAL)
        self.assertEqual(result.am_direction, Direction.A_TO_B)
        self.assertEqual(result.pm_direction, Direction.B_TO_A)

    def test_same_direction_is_not_reversal(self):
        self.assertEqual(detect_reversal(12, 4, 15, 5).reversal_status, ReversalStatus.NO_REVERSAL)

    def test_am_ba_pm_ab_reversal(self):
        self.assertEqual(detect_reversal(4, 12, 14, 3).reversal_status, ReversalStatus.REVERSAL)

    def test_equal_counts_have_no_clear_direction(self):
        result = get_main_direction(8, 8)
        self.assertEqual(result.status, DirectionStatus.NO_CLEAR_DIRECTION)
        self.assertIsNone(result.direction)

    def test_below_minimum_is_low_traffic(self):
        result = get_main_direction(6, 3)
        self.assertEqual(result.status, DirectionStatus.LOW_TRAFFIC)

    def test_missing_am_makes_result_undetermined(self):
        result = detect_reversal(None, 0, 5, 15)
        self.assertEqual(result.am_status, DirectionStatus.MISSING_DATA)
        self.assertEqual(result.reversal_status, ReversalStatus.UNDETERMINED)

    def test_missing_pm_makes_result_undetermined(self):
        result = detect_reversal(15, 5, 0, None)
        self.assertEqual(result.pm_status, DirectionStatus.MISSING_DATA)
        self.assertEqual(result.reversal_status, ReversalStatus.UNDETERMINED)

    def test_observed_zeros_are_low_traffic_not_missing(self):
        result = get_main_direction(0, 0)
        self.assertEqual(result.status, DirectionStatus.LOW_TRAFFIC)
        self.assertIsNone(result.dominance)

    def test_exact_dominance_threshold_passes(self):
        result = get_main_direction(10, 5)
        self.assertEqual(result.status, DirectionStatus.VALID)
        self.assertAlmostEqual(result.dominance, 2 / 3)

    def test_custom_thresholds_apply(self):
        result = get_main_direction(4, 2, min_total_rides=6, min_dominance=0.6)
        self.assertEqual(result.status, DirectionStatus.VALID)
        self.assertEqual(result.direction, Direction.A_TO_B)

    def test_invalid_negative_or_noninteger_counts_raise(self):
        for counts in [(-1, 10), (1, -1), (1.5, 10), (True, 10)]:
            with self.subTest(counts=counts), self.assertRaises(ValueError):
                get_main_direction(*counts)

    def test_invalid_thresholds_raise(self):
        with self.assertRaises(ValueError):
            get_main_direction(5, 5, min_total_rides=-1)
        with self.assertRaises(ValueError):
            get_main_direction(5, 5, min_dominance=1.1)


if __name__ == "__main__":
    unittest.main()
