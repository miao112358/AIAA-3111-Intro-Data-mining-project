"""Simulated-data tests for daily transaction construction."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from transaction_builder import (  # noqa: E402
    BuilderConfig, FieldMapping, build_direction_records, build_transactions,
    export_results, validate_transactions,
)


def row(day, window, origin, destination, count, scale="zone", day_type="weekday"):
    return {"date": day, "day_type": day_type, "time_window": window,
            "spatial_scale": scale, "origin": origin, "destination": destination,
            "ride_count": count}


class TransactionBuilderTests(unittest.TestCase):
    def setUp(self):
        self.coverage = [
            {"date": "2025-01-06", "is_complete": True},
            {"date": "2025-01-07", "is_complete": True},
            {"date": "2025-01-08", "is_complete": False},
            {"date": "2025-10-01", "is_complete": True},
        ]
        self.od = [
            row("2025-01-06", "AM", "A", "B", 18), row("2025-01-06", "AM", "B", "A", 6),
            row("2025-01-06", "PM", "A", "B", 5), row("2025-01-06", "PM", "B", "A", 15),
            row("2025-01-07", "AM", "A", "B", 12), row("2025-01-07", "AM", "B", "A", 4),
            row("2025-01-07", "PM", "A", "B", 15), row("2025-01-07", "PM", "B", "A", 5),
            row("2025-01-08", "AM", "A", "B", 20), row("2025-01-08", "AM", "B", "A", 1),
            row("2025-10-01", "MID", "C", "D", 10), row("2025-10-01", "MID", "D", "C", 5),
        ]

    def build(self, od=None, coverage=None, config=None):
        return build_transactions(od if od is not None else self.od,
                                 coverage if coverage is not None else self.coverage,
                                 config or BuilderConfig(spatial_scale="zone", missing_od_row_policy="zero"))

    def test_reversal_keeps_am_and_pm_items_separate(self):
        result = self.build()
        self.assertEqual(result.transactions["2025-01-06"], ["AM:A->B", "PM:B->A"])

    def test_same_direction_keeps_both_items(self):
        result = self.build()
        self.assertEqual(result.transactions["2025-01-07"], ["AM:A->B", "PM:A->B"])

    def test_complete_empty_transaction_is_retained(self):
        self.assertEqual(self.build().transactions["2025-01-07"], ["AM:A->B", "PM:A->B"])
        result = self.build([], [{"date": "2025-01-09", "is_complete": True}])
        self.assertEqual(result.transactions, {"2025-01-09": []})

    def test_incomplete_date_excluded_and_audited(self):
        result = self.build()
        self.assertNotIn("2025-01-08", result.transactions)
        self.assertEqual(result.excluded_dates, ["2025-01-08"])
        self.assertEqual(result.valid_denominator, 2)
        self.assertEqual(result.holdout_denominator, 1)

    def test_unclear_and_low_traffic_directions_make_no_item(self):
        od = [row("2025-01-06", "AM", "A", "B", 8), row("2025-01-06", "AM", "B", "A", 8),
              row("2025-01-06", "PM", "A", "B", 4), row("2025-01-06", "PM", "B", "A", 3)]
        result = self.build(od, [{"date": "2025-01-06", "is_complete": True}])
        self.assertEqual(result.transactions["2025-01-06"], [])

    def test_explicit_missing_count_is_not_zero(self):
        od = [row("2025-01-06", "AM", "A", "B", None)]
        records = build_direction_records(od, [{"date": "2025-01-06", "is_complete": True}],
                                          BuilderConfig(spatial_scale="zone", missing_od_row_policy="zero"))
        self.assertEqual(records[0]["status"], "MISSING_DATA")

    def test_multiple_corridors_and_item_traceability(self):
        od = self.od + [row("2025-01-06", "AM", "C", "D", 12), row("2025-01-06", "AM", "D", "C", 3)]
        result = self.build(od)
        self.assertIn("AM:C->D", result.transactions["2025-01-06"])
        self.assertEqual(result.item_sources["AM:C->D"][0]["date"], "2025-01-06")
        self.assertEqual(result.item_sources["AM:C->D"][0]["spatial_scale"], "zone")

    def test_scales_cannot_be_mixed(self):
        with self.assertRaises(ValueError):
            self.build(self.od + [row("2025-01-06", "AM", "A", "B", 2, "station")])

    def test_all_windows_supported(self):
        result = self.build()
        self.assertIn("MID:C->D", result.holdout_transactions["2025-10-01"])
        self.assertEqual(result.transactions["2025-01-06"], ["AM:A->B", "PM:B->A"])

    def test_development_and_holdout_labels(self):
        result = self.build()
        splits = {(r["date"], r["split"]) for r in result.direction_records}
        self.assertIn(("2025-01-06", "Development"), splits)
        self.assertIn(("2025-10-01", "Holdout"), splits)
        result = self.build()
        self.assertNotIn("2025-10-01", result.development_transactions)
        self.assertIn("2025-10-01", result.holdout_transactions)

    def test_custom_field_mapping(self):
        od = [{"d": "2025-01-06", "w": "AM", "s": "zone", "o": "A", "dest": "B", "n": 12}]
        coverage = [{"d": "2025-01-06", "ok": True}]
        mapping = FieldMapping(date="d", time_window="w", spatial_scale="s", origin="o",
                               destination="dest", ride_count="n", is_complete="ok", day_type="kind")
        result = build_transactions(od, coverage, BuilderConfig(spatial_scale="zone",
                                                               missing_od_row_policy="zero", fields=mapping))
        self.assertEqual(result.transactions["2025-01-06"], ["AM:A->B"])

    def test_no_duplicate_items_and_validator_passes(self):
        result = self.build()
        self.assertEqual(validate_transactions(result, self.coverage, BuilderConfig(spatial_scale="zone")), [])
        self.assertEqual(len(result.transactions["2025-01-06"]), len(set(result.transactions["2025-01-06"])))

    def test_invalid_directional_data_is_rejected(self):
        with self.assertRaises(ValueError):
            self.build([row("2025-01-06", "AM", "A", "A", 11)], self.coverage[:1])
        with self.assertRaises(ValueError):
            self.build([row("2025-01-06", "AM", "A", "B", -2)], self.coverage[:1])

    def test_exports_four_expected_files(self):
        result = self.build()
        with tempfile.TemporaryDirectory() as temp_dir:
            export_results(result, temp_dir)
            self.assertEqual(set(p.name for p in Path(temp_dir).iterdir()),
                             {"transactions.json", "transactions.csv", "holdout_transactions.csv",
                              "outside_study_period_transactions.csv", "direction_records.csv", "transaction_audit.csv"})

    def test_strict_boolean_values(self):
        result = self.build([], [{"date": "2025-01-09", "is_complete": "false"}])
        self.assertEqual(result.development_transactions, {})
        for invalid in ("nope", "", None, 2):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.build([], [{"date": "2025-01-09", "is_complete": invalid}])

    def test_duplicate_coverage_rejected_by_both_entry_points(self):
        coverage = [{"date": "2025-01-06", "is_complete": True}] * 2
        with self.assertRaises(ValueError):
            build_transactions([], coverage)
        with self.assertRaises(ValueError):
            build_direction_records([], coverage)

    def test_unknown_window_rejected(self):
        with self.assertRaises(ValueError):
            self.build([row("2025-01-06", "MORNING", "A", "B", 12)], self.coverage[:1])

    def test_missing_row_policies(self):
        od = [row("2025-01-06", "AM", "A", "B", 12)]
        coverage = [{"date": "2025-01-06", "is_complete": True}]
        with self.assertRaisesRegex(ValueError, "Missing OD row"):
            build_transactions(od, coverage, BuilderConfig(spatial_scale="zone"))
        missing = build_transactions(od, coverage, BuilderConfig(spatial_scale="zone", missing_od_row_policy="missing"))
        self.assertEqual(missing.direction_records[0]["status"], "MISSING_DATA")
        zero = build_transactions(od, coverage, BuilderConfig(spatial_scale="zone", missing_od_row_policy="zero"))
        self.assertEqual(zero.transactions, {"2025-01-06": ["AM:A->B"]})

    def test_partition_denominators_and_outside_period(self):
        coverage = [
            {"date": "2024-12-31", "is_complete": True},
            {"date": "2025-01-01", "is_complete": True},
            {"date": "2025-10-01", "is_complete": True},
            {"date": "2026-01-01", "is_complete": True},
        ]
        result = self.build([], coverage)
        self.assertEqual(result.development_denominator, 1)
        self.assertEqual(result.holdout_denominator, 1)
        self.assertEqual(result.outside_denominator, 2)
        self.assertEqual(result.transactions, result.development_transactions)
        self.assertEqual(set(result.development_transactions), {"2025-01-01"})
        self.assertEqual(set(result.holdout_transactions), {"2025-10-01"})
        self.assertEqual(set(result.outside_transactions), {"2024-12-31", "2026-01-01"})

    def test_input_record_ids_and_aggregation_keys_are_preserved(self):
        od = [{**row("2025-01-06", "AM", "A", "B", 12), "row_id": "r1"},
              {**row("2025-01-06", "AM", "B", "A", 0), "row_id": "r2"}]
        config = BuilderConfig(spatial_scale="zone", missing_od_row_policy="zero",
                               fields=FieldMapping(record_id="row_id"))
        result = self.build(od, self.coverage[:1], config)
        source = result.item_sources["AM:A->B"][0]
        self.assertEqual(source["source_ids_ab"], ["r1"])
        self.assertEqual(source["aggregation_key_ab"], ["2025-01-06", "zone", "AM", "A", "B"])


if __name__ == "__main__":
    unittest.main()
