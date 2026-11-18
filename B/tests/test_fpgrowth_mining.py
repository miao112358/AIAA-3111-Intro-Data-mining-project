"""Simulated-data tests for mlxtend FP-Growth and Apriori parity."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from apriori_mining import mine_development as mine_apriori
from fpgrowth_mining import export_fpgrowth, mine_fpgrowth, run_fpgrowth


class FPGrowthTests(unittest.TestCase):
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

    def test_single_pair_support_and_empty_denominator(self):
        found = run_fpgrowth(self.transactions, 0.25, max_len=2)
        support = {frozenset(items): value for items, value in zip(found.itemsets, found.support)}
        self.assertAlmostEqual(support[frozenset({"AM:A->B"})], 3 / 4)
        self.assertAlmostEqual(support[frozenset({"PM:B->A"})], 2 / 4)
        self.assertAlmostEqual(support[frozenset({"AM:A->B", "PM:B->A"})], 2 / 4)
        # The empty fourth date is reflected in the 3/4 and 2/4 supports above.

    def test_infrequent_itemsets_are_removed(self):
        found = run_fpgrowth(self.transactions, 0.75, max_len=2)
        self.assertNotIn(frozenset({"PM:B->A"}), set(found.itemsets))

    def test_reversal_pattern_filtering(self):
        run = mine_fpgrowth(self.transactions, 0.5, item_metadata=self.metadata, spatial_scale="zone")
        self.assertEqual(len(run.reversal_patterns), 1)
        self.assertEqual(run.reversal_patterns.iloc[0].corridor["area_a"], "A")

    def test_same_direction_and_cross_corridor_not_reversals(self):
        transactions = {"d1": ["AM:A->B", "PM:A->B"], "d2": ["AM:A->B", "PM:D->C"]}
        run = mine_fpgrowth(transactions, 0.5, item_metadata=self.metadata)
        self.assertEqual(len(run.reversal_patterns), 0)

    def test_max_len_and_bad_parameters(self):
        found = run_fpgrowth(self.transactions, 0.25, max_len=2)
        self.assertLessEqual(max(found.itemset_length), 2)
        for support in (0, -0.1, 1.1, True):
            with self.subTest(support=support), self.assertRaises(ValueError):
                run_fpgrowth(self.transactions, support)
        with self.assertRaises(ValueError):
            run_fpgrowth(self.transactions, 0.5, max_len=0)

    def test_all_empty_transactions(self):
        run = mine_fpgrowth({"d1": [], "d2": []}, 0.5)
        self.assertTrue(run.frequent_itemsets.empty)
        self.assertEqual(run.metadata["development_date_count"], 2)
        self.assertEqual(run.metadata["empty_transaction_count"], 2)

    def test_holdout_partition_is_not_used(self):
        class BuildResult:
            development_transactions = {"2025-01-01": ["AM:A->B"], "2025-01-02": []}
            holdout_transactions = {"2025-10-01": ["AM:X->Y"]}
            outside_transactions = {"2024-12-31": ["AM:Q->R"]}
            direction_records = []
            spatial_scale = "zone"
        # The same API accepts the development mapping explicitly; BuildResult path is checked below.
        from fpgrowth_mining import mine_build_result
        run = mine_build_result(BuildResult(), 0.5)
        self.assertEqual(run.metadata["development_date_count"], 2)
        self.assertEqual(set(run.encoded.columns), {"AM:A->B"})

    def test_apriori_and_fpgrowth_itemsets_supports_and_reversals_match(self):
        apriori = mine_apriori(self.transactions, 0.25, 2, spatial_scale="zone", item_metadata=self.metadata)
        fp = mine_fpgrowth(self.transactions, 0.25, 2, spatial_scale="zone", item_metadata=self.metadata)
        standardize = lambda frame: {tuple(sorted(items)): round(float(support), 12)
                                     for items, support in zip(frame.itemsets, frame.support)}
        self.assertEqual(standardize(apriori.frequent_itemsets), standardize(fp.frequent_itemsets))
        self.assertEqual(standardize(apriori.reversal_patterns), standardize(fp.reversal_patterns))

    def test_exported_files_and_metadata(self):
        run = mine_fpgrowth(self.transactions, 0.25, item_metadata=self.metadata, spatial_scale="zone")
        with tempfile.TemporaryDirectory() as tmp:
            export_fpgrowth(run, tmp)
            self.assertEqual(set(p.name for p in Path(tmp).iterdir()), {
                "fpgrowth_frequent_itemsets.csv", "fpgrowth_reversal_patterns.csv", "fpgrowth_run_metadata.json"})
            metadata = json.loads((Path(tmp) / "fpgrowth_run_metadata.json").read_text())
            self.assertEqual(metadata["algorithm"], "FP-Growth")
            self.assertEqual(metadata["algorithm_version"], "0.25.0")


if __name__ == "__main__":
    unittest.main()
