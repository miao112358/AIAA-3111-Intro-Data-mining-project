"""Simulated transaction tests for Apriori mining and reversal filtering."""

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from apriori_mining import (  # noqa: E402
    build_item_metadata, encode_transactions, export_apriori, filter_reversal_patterns,
    mine_build_result, mine_development, run_apriori, apriori as mlxtend_apriori,
)

requires_mlxtend = unittest.skipIf(mlxtend_apriori is None, "mlxtend is not installed in this environment")


class AprioriMiningTests(unittest.TestCase):
    def setUp(self):
        self.transactions = {
            "2025-01-01": ["AM:A->B", "PM:B->A"],
            "2025-01-02": ["AM:A->B", "PM:B->A"],
            "2025-01-03": ["AM:A->B"],
            "2025-01-04": [],
        }
        self.metadata = {
            "AM:A->B": {"window": "AM", "spatial_scale": "zone", "area_a": "A", "area_b": "B",
                         "origin": "A", "destination": "B"},
            "PM:B->A": {"window": "PM", "spatial_scale": "zone", "area_a": "A", "area_b": "B",
                         "origin": "B", "destination": "A"},
            "PM:A->B": {"window": "PM", "spatial_scale": "zone", "area_a": "A", "area_b": "B",
                         "origin": "A", "destination": "B"},
            "PM:D->C": {"window": "PM", "spatial_scale": "zone", "area_a": "C", "area_b": "D",
                         "origin": "D", "destination": "C"},
        }

    def test_boolean_encoding_retains_empty_date(self):
        encoded = encode_transactions(self.transactions)
        self.assertEqual(encoded.shape, (4, 2))
        self.assertEqual(list(encoded.index), sorted(self.transactions))
        self.assertFalse(encoded.loc["2025-01-04"].any())
        self.assertTrue(all(dtype == bool for dtype in encoded.dtypes))

    @requires_mlxtend
    def test_single_and_pair_itemsets_and_manual_support(self):
        result = run_apriori(self.transactions, min_support=0.25, max_len=2)
        supports = {frozenset(items): support for items, support in zip(result.itemsets, result.support)}
        self.assertAlmostEqual(supports[frozenset({"AM:A->B"})], 3 / 4)
        self.assertAlmostEqual(supports[frozenset({"PM:B->A"})], 2 / 4)
        self.assertAlmostEqual(supports[frozenset({"AM:A->B", "PM:B->A"})], 2 / 4)

    @requires_mlxtend
    def test_nonfrequent_itemsets_are_filtered_and_max_len_applies(self):
        result = run_apriori(self.transactions, min_support=0.75, max_len=2)
        self.assertTrue(all(length <= 2 for length in result.itemset_length))
        self.assertNotIn(frozenset({"PM:B->A"}), set(result.itemsets))
        self.assertNotIn(frozenset({"AM:A->B", "PM:B->A"}), set(result.itemsets))

    @requires_mlxtend
    def test_reversal_filter_uses_corridor_and_direction_metadata(self):
        frequent = run_apriori(self.transactions, min_support=0.5, max_len=2)
        reversals = filter_reversal_patterns(frequent, self.metadata)
        self.assertEqual(len(reversals), 1)
        self.assertEqual(reversals.iloc[0].corridor["area_a"], "A")
        self.assertEqual(set(reversals.iloc[0].itemsets), {"AM:A->B", "PM:B->A"})

    @requires_mlxtend
    def test_same_direction_and_cross_corridor_pairs_are_filtered(self):
        tx = {"d1": ["AM:A->B", "PM:A->B"], "d2": ["AM:A->B", "PM:D->C"]}
        # Both combinations are frequent; metadata proves neither is a valid reversal pair.
        frequent = run_apriori(tx, min_support=0.5, max_len=2)
        self.assertEqual(len(filter_reversal_patterns(frequent, self.metadata)), 0)

    def test_all_empty_transactions_return_no_patterns(self):
        result = mine_development({"d1": [], "d2": []}, 0.5)
        self.assertEqual(result.frequent_itemsets.empty, True)
        self.assertEqual(result.metadata["development_date_count"], 2)
        self.assertEqual(result.metadata["empty_transaction_count"], 2)

    def test_empty_input_raises_and_parameters_are_validated(self):
        with self.assertRaises(ValueError):
            run_apriori({}, 0.5)
        for support in (0, -0.1, 1.1, float("nan"), True):
            with self.subTest(support=support), self.assertRaises(ValueError):
                run_apriori(self.transactions, support)
        with self.assertRaises(ValueError):
            run_apriori(self.transactions, 0.5, max_len=0)

    @requires_mlxtend
    def test_scale_runs_are_separate_and_holdout_not_passed_to_mining(self):
        class MockBuildResult:
            development_transactions = {"2025-01-01": ["AM:A->B"], "2025-01-02": []}
            holdout_transactions = {"2025-10-01": ["AM:X->Y"]}
            outside_transactions = {"2024-12-31": ["AM:Q->R"]}
            spatial_scale = "zone"
            direction_records = []

        first = mine_build_result(MockBuildResult(), 0.5)
        self.assertEqual(first.metadata["input_partition"], "Development")
        self.assertEqual(first.metadata["development_date_count"], 2)
        second = mine_development({"d1": ["AM:A->B"], "d2": []}, 0.5, spatial_scale="station")
        self.assertEqual(second.metadata["spatial_scale"], "station")
        self.assertEqual(set(first.encoded.columns), {"AM:A->B"})

    def test_metadata_from_transaction_builder_records(self):
        records = [{"status": "VALID", "direction": "A_TO_B", "time_window": "AM",
                    "spatial_scale": "zone", "area_a": "A", "area_b": "B"}]
        self.assertEqual(build_item_metadata(records)["AM:A->B"]["area_b"], "B")

    def test_reversal_filter_matches_metadata_without_parsing_labels(self):
        itemsets = pd.DataFrame([{
            "itemsets": frozenset({"unstructured-am-item", "unstructured-pm-item"}),
            "support": 0.5, "itemset_length": 2, "dates": ["2025-01-01"], "corridor": None,
        }])
        metadata = {
            "unstructured-am-item": {"window": "AM", "spatial_scale": "zone", "area_a": "A", "area_b": "B",
                                      "origin": "A", "destination": "B"},
            "unstructured-pm-item": {"window": "PM", "spatial_scale": "zone", "area_a": "A", "area_b": "B",
                                      "origin": "B", "destination": "A"},
        }
        self.assertEqual(len(filter_reversal_patterns(itemsets, metadata)), 1)

    @requires_mlxtend
    def test_export_writes_three_artifacts(self):
        result = mine_development(self.transactions, 0.5, item_metadata=self.metadata)
        with tempfile.TemporaryDirectory() as tmp:
            export_apriori(result, tmp)
            self.assertEqual(set(p.name for p in Path(tmp).iterdir()), {
                "apriori_frequent_itemsets.csv", "apriori_reversal_patterns.csv", "apriori_run_metadata.json"})


if __name__ == "__main__":
    unittest.main()
