"""E0 integration checks for shared direction-event pipeline."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from build_direction_events import build_direction_events  # noqa: E402
from transaction_builder import BuilderConfig


class DirectionEventPipelineTests(unittest.TestCase):
    def setUp(self):
        self.coverage = [{"date": "2025-01-06", "is_complete": True},
                         {"date": "2025-01-07", "is_complete": True}]
        self.rows = [
            {"date": "2025-01-06", "day_type": "weekday", "time_window": "AM", "spatial_scale": "1000m", "origin": "A", "destination": "B", "ride_count": 18},
            {"date": "2025-01-06", "day_type": "weekday", "time_window": "AM", "spatial_scale": "1000m", "origin": "B", "destination": "A", "ride_count": 6},
            {"date": "2025-01-06", "day_type": "weekday", "time_window": "PM", "spatial_scale": "1000m", "origin": "A", "destination": "B", "ride_count": 5},
            {"date": "2025-01-06", "day_type": "weekday", "time_window": "PM", "spatial_scale": "1000m", "origin": "B", "destination": "A", "ride_count": 15},
            {"date": "2025-01-07", "day_type": "weekday", "time_window": "AM", "spatial_scale": "1000m", "origin": "A", "destination": "B", "ride_count": 12},
            {"date": "2025-01-07", "day_type": "weekday", "time_window": "AM", "spatial_scale": "1000m", "origin": "B", "destination": "A", "ride_count": 12},
            {"date": "2025-01-07", "day_type": "weekday", "time_window": "PM", "spatial_scale": "1000m", "origin": "A", "destination": "B", "ride_count": 4},
            {"date": "2025-01-07", "day_type": "weekday", "time_window": "PM", "spatial_scale": "1000m", "origin": "B", "destination": "A", "ride_count": 3},
        ]

    def test_reversal_and_no_clear_events_and_empty_transaction(self):
        data = self.rows[:4]
        result = build_direction_events(data, self.coverage,
            grid_size_m=1000, config=BuilderConfig(spatial_scale="1000m", missing_od_row_policy="zero"))
        statuses = {e["date"]: e["reversal_status"] for e in result["direction_events"]}
        self.assertEqual(statuses["2025-01-06"], "REVERSAL")
        self.assertEqual(statuses["2025-01-07"], "UNDETERMINED")
        self.assertEqual(result["development_transactions"]["2025-01-07"], [])

    def test_scale_switch_and_unknown_coverage_rejected(self):
        rows = [dict(r, spatial_scale="500m") for r in self.rows]
        out = build_direction_events(rows, self.coverage, grid_size_m=500,
            config=BuilderConfig(spatial_scale="500m", missing_od_row_policy="zero"))
        self.assertEqual(out["build_result"].spatial_scale, "500m")
        with self.assertRaises(ValueError):
            build_direction_events(rows, [{"date": "2025-01-06", "is_complete": None}], grid_size_m=500,
                config=BuilderConfig(spatial_scale="500m", missing_od_row_policy="zero"))


if __name__ == "__main__":
    unittest.main()
