"""Tests for reproducible Development-only algorithm benchmarking."""

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import algorithm_benchmark as benchmark  # noqa: E402


class BenchmarkTests(unittest.TestCase):
    def small_data(self):
        return ({"d1": ["AM:A->B", "PM:B->A"], "d2": ["AM:A->B"], "d3": []}, {
            "AM:A->B": {"window": "AM", "spatial_scale": "zone", "area_a": "A", "area_b": "B",
                         "origin": "A", "destination": "B"},
            "PM:B->A": {"window": "PM", "spatial_scale": "zone", "area_a": "A", "area_b": "B",
                         "origin": "B", "destination": "A"},
        })

    def test_generator_is_deterministic_and_sizes_work(self):
        first, _ = benchmark.generate_simulated_transactions(20, 10, 3, seed=9)
        second, _ = benchmark.generate_simulated_transactions(20, 10, 3, seed=9)
        self.assertEqual(first, second)
        for size in (500, 2000, 5000):
            generated, _ = benchmark.generate_simulated_transactions(size, 20, 4, seed=7)
            self.assertEqual(len(generated), size)

    def test_shared_parameters_results_and_runtime_statistics(self):
        transactions, metadata = self.small_data()
        with patch.object(benchmark, "_measure_peak_rss", return_value=123456):
            rows = benchmark.benchmark_transactions(transactions, 0.3, 2, 5,
                                                    item_metadata=metadata, spatial_scale="zone")
        self.assertEqual({r["algorithm"] for r in rows}, {"Apriori", "FP-Growth"})
        for row in rows:
            self.assertEqual(row["min_support"], 0.3)
            self.assertEqual(row["max_len"], 2)
            self.assertEqual(row["repeat_count"], 5)
            self.assertGreaterEqual(row["encoding_time"], 0)
            self.assertGreaterEqual(row["mining_time"], 0)
            self.assertGreaterEqual(row["end_to_end_time"], 0)
            self.assertEqual(row["peak_memory"], 123456)
            self.assertTrue(row["result_consistent"])
        self.assertEqual(rows[0]["frequent_itemset_count"], rows[1]["frequent_itemset_count"])

    def test_inconsistency_is_reported(self):
        transactions, metadata = self.small_data()
        actual = benchmark._raw_mine
        def changed(name, encoded, support, max_len):
            result = actual(name, encoded, support, max_len)
            if name == "FP-Growth" and not result.empty:
                result.loc[0, "support"] = min(1.0, result.loc[0, "support"] + 0.1)
            return result
        with patch.object(benchmark, "_raw_mine", side_effect=changed), \
             patch.object(benchmark, "_measure_peak_rss", return_value=1):
            with self.assertRaisesRegex(RuntimeError, "outputs differ"):
                benchmark.benchmark_transactions(transactions, 0.3, 2, 5, item_metadata=metadata)

    def test_density_must_match_average_length(self):
        with self.assertRaises(ValueError):
            benchmark.generate_simulated_transactions(10, item_count=10, average_length=2, item_density=0.8)

    def test_holdout_and_outside_are_not_accessed(self):
        class Build:
            development_transactions = {"d1": ["AM:A->B"], "d2": []}
            direction_records = []
            spatial_scale = "zone"
            @property
            def holdout_transactions(self):
                raise AssertionError("Holdout accessed")
            @property
            def outside_transactions(self):
                raise AssertionError("Outside partition accessed")
        with patch.object(benchmark, "_measure_peak_rss", return_value=1):
            rows = benchmark.benchmark_build_result(Build(), 0.5, repeat_count=5)
        self.assertTrue(all(row["dataset_size"] == 2 for row in rows))

    def test_invalid_parameters(self):
        tx, _ = self.small_data()
        for args in [(0, 2, 5), (1.1, 2, 5), (0.5, 0, 5), (0.5, 2, 4)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                benchmark.benchmark_transactions(tx, args[0], args[1], args[2])
        with self.assertRaises(ValueError):
            benchmark.generate_simulated_transactions(0)

    def test_exports_csv_and_json_metadata(self):
        row = {"algorithm": "Apriori", "dataset_size": 4, "result_consistent": True}
        with tempfile.TemporaryDirectory() as tmp:
            benchmark.export_benchmark([row], tmp)
            self.assertEqual(set(p.name for p in Path(tmp).iterdir()),
                             {"algorithm_benchmark.csv", "algorithm_benchmark.json"})
            with (Path(tmp) / "algorithm_benchmark.csv").open() as handle:
                self.assertEqual(next(csv.DictReader(handle))["algorithm"], "Apriori")
            payload = json.loads((Path(tmp) / "algorithm_benchmark.json").read_text())
            self.assertTrue(payload["measurement_notes"]["synthetic_data_only"])


if __name__ == "__main__":
    unittest.main()
