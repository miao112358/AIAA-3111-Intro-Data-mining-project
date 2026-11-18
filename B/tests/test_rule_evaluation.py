"""Simulated rule metric and Development/Holdout isolation tests."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rule_evaluation import (  # noqa: E402
    calculate_rule_metrics, construct_reversal_rules, evaluate_development_rules,
    evaluate_holdout_rules, export_rule_evaluation,
)


def metadata():
    return {
        "AM:A->B": {"window": "AM", "spatial_scale": "zone", "area_a": "A", "area_b": "B", "origin": "A", "destination": "B"},
        "PM:B->A": {"window": "PM", "spatial_scale": "zone", "area_a": "A", "area_b": "B", "origin": "B", "destination": "A"},
        "PM:A->B": {"window": "PM", "spatial_scale": "zone", "area_a": "A", "area_b": "B", "origin": "A", "destination": "B"},
        "AM:C->D": {"window": "AM", "spatial_scale": "zone", "area_a": "C", "area_b": "D", "origin": "C", "destination": "D"},
        "PM:D->C": {"window": "PM", "spatial_scale": "zone", "area_a": "C", "area_b": "D", "origin": "D", "destination": "C"},
        "AM:E->F": {"window": "AM", "spatial_scale": "zone", "area_a": "E", "area_b": "F", "origin": "E", "destination": "F"},
        "PM:F->E": {"window": "PM", "spatial_scale": "zone", "area_a": "E", "area_b": "F", "origin": "F", "destination": "E"},
        "PM:B->A_station": {"window": "PM", "spatial_scale": "station", "area_a": "A", "area_b": "B", "origin": "B", "destination": "A"},
    }


class RuleEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.dev = {
            "2025-01-01": ["AM:A->B", "PM:B->A", "AM:C->D", "PM:D->C", "AM:E->F", "PM:F->E"],
            "2025-01-02": ["AM:A->B", "PM:B->A", "AM:C->D", "PM:D->C", "AM:E->F"],
            "2025-01-03": ["AM:A->B", "PM:A->B", "AM:E->F"],
            "2025-01-04": [],  # Complete empty day belongs in denominator.
        }
        self.item_metadata = metadata()
        self.frequent = pd.DataFrame({"itemsets": [
            frozenset(("AM:A->B", "PM:B->A")),
            frozenset(("AM:C->D", "PM:D->C")),
            frozenset(("AM:E->F", "PM:F->E")),
            frozenset(("AM:A->B", "PM:A->B")),  # same direction
            frozenset(("AM:A->B", "PM:D->C")),  # cross corridor
        ]})

    def test_support_confidence_lift_hand_calculation(self):
        metrics = calculate_rule_metrics(self.dev, "AM:A->B", "PM:B->A", corridor="A<->B", spatial_scale="zone")
        self.assertEqual((metrics["total_complete_days"], metrics["antecedent_count"],
                          metrics["consequent_count"], metrics["joint_count"]), (4, 3, 2, 2))
        self.assertAlmostEqual(metrics["support"], 2 / 4)
        self.assertAlmostEqual(metrics["confidence"], 2 / 3)
        self.assertAlmostEqual(metrics["lift"], (2 / 3) / (2 / 4))

    def test_three_rules_match_manual_metrics(self):
        all_rules, _ = evaluate_development_rules(self.dev, self.frequent, self.item_metadata)
        by_antecedent = all_rules.set_index("antecedent")
        # A-B: joint 2, antecedent 3, consequent 2, N=4.
        self.assertAlmostEqual(by_antecedent.loc["AM:A->B", "support"], 0.5)
        self.assertAlmostEqual(by_antecedent.loc["AM:A->B", "confidence"], 2 / 3)
        self.assertAlmostEqual(by_antecedent.loc["AM:A->B", "lift"], 4 / 3)
        # C-D: joint 2, antecedent 2, consequent 2, N=4.
        self.assertAlmostEqual(by_antecedent.loc["AM:C->D", "support"], 0.5)
        self.assertAlmostEqual(by_antecedent.loc["AM:C->D", "confidence"], 1.0)
        self.assertAlmostEqual(by_antecedent.loc["AM:C->D", "lift"], 2.0)
        # E-F: joint 1, antecedent 3, consequent 1, N=4.
        self.assertAlmostEqual(by_antecedent.loc["AM:E->F", "support"], 0.25)
        self.assertAlmostEqual(by_antecedent.loc["AM:E->F", "confidence"], 1 / 3)
        self.assertAlmostEqual(by_antecedent.loc["AM:E->F", "lift"], 4 / 3)

    def test_empty_day_in_denominator_and_dates_traceable(self):
        metrics = calculate_rule_metrics(self.dev, "AM:A->B", "PM:B->A")
        self.assertEqual(metrics["total_complete_days"], 4)
        self.assertEqual(metrics["joint_dates"], ["2025-01-01", "2025-01-02"])

    def test_incomplete_dates_excluded_by_development_input(self):
        complete_dev = dict(self.dev)
        # Transaction Builder's Development map excludes incomplete dates.
        complete_dev.pop("2025-01-04")
        metrics = calculate_rule_metrics(complete_dev, "AM:A->B", "PM:B->A")
        self.assertEqual(metrics["total_complete_days"], 3)
        self.assertEqual(metrics["support"], 2 / 3)

    def test_only_am_to_pm_same_corridor_opposite_direction_rules(self):
        rules = construct_reversal_rules(self.frequent, self.item_metadata)
        self.assertEqual({r["antecedent"] for r in rules}, {"AM:A->B", "AM:C->D", "AM:E->F"})
        self.assertTrue(all(r["consequent"].startswith("PM:") for r in rules))

    def test_space_scale_must_match(self):
        altered = dict(self.item_metadata)
        altered["PM:B->A"] = dict(altered["PM:B->A"], spatial_scale="station")
        rules = construct_reversal_rules(self.frequent.iloc[:1], altered)
        self.assertEqual(rules, [])

    def test_custom_thresholds_keep_full_table(self):
        full, filtered = evaluate_development_rules(self.dev, self.frequent, self.item_metadata,
                                                   min_support=0.3, min_confidence=0.8, min_lift=1.5)
        self.assertEqual(len(full), 3)
        self.assertEqual(set(filtered["antecedent"]), {"AM:C->D"})

    def test_zero_denominator_has_no_nan_or_fabricated_metrics(self):
        result = calculate_rule_metrics({}, "AM:A->B", "PM:B->A")
        self.assertEqual(result["status"], "UNESTIMABLE_ZERO_DENOMINATOR")
        self.assertIsNone(result["support"])
        self.assertIsNone(result["confidence"])
        self.assertIsNone(result["lift"])

    def test_zero_antecedent_and_zero_consequent_are_unestimable(self):
        no_ant = calculate_rule_metrics({"d1": [], "d2": ["PM:B->A"]}, "AM:A->B", "PM:B->A")
        self.assertEqual(no_ant["status"], "UNESTIMABLE_NO_ANTECEDENT")
        zero_cons = calculate_rule_metrics({"d1": ["AM:A->B"], "d2": []}, "AM:A->B", "PM:B->A")
        self.assertEqual(zero_cons["status"], "UNESTIMABLE_ZERO_CONSEQUENT_SUPPORT")
        self.assertEqual(zero_cons["confidence"], 0.0)
        self.assertIsNone(zero_cons["lift"])

    def test_holdout_evaluates_locked_development_rules_only(self):
        full, _ = evaluate_development_rules(self.dev, self.frequent, self.item_metadata)
        holdout = {"2025-10-01": ["AM:A->B", "PM:B->A"], "2025-10-02": []}
        scored = evaluate_holdout_rules(full, holdout)
        self.assertTrue(set(scored["evaluation_split"]) == {"Holdout"})
        row = scored.set_index("antecedent").loc["AM:A->B"]
        self.assertEqual(row["total_complete_days"], 2)
        self.assertEqual(row["joint_count"], 1)
        self.assertEqual(row["support"], 0.5)
        self.assertEqual(row["confidence"], 1.0)
        self.assertEqual(row["lift"], 2.0)
        absent = scored.set_index("antecedent").loc["AM:C->D"]
        self.assertEqual(absent["status"], "UNESTIMABLE_NO_ANTECEDENT")
        self.assertIsNone(absent["confidence"])
        self.assertIsNone(absent["lift"])
        with self.assertRaises(ValueError):
            evaluate_holdout_rules(scored, holdout)

    def test_export_preserves_counts_metrics_and_split(self):
        full, selected = evaluate_development_rules(self.dev, self.frequent, self.item_metadata)
        holdout = evaluate_holdout_rules(full, {"h1": ["AM:A->B", "PM:B->A"], "h2": []})
        with tempfile.TemporaryDirectory() as tmp:
            export_rule_evaluation(full, holdout, tmp, total_development_days=4, total_holdout_days=2)
            output = pd.read_csv(Path(tmp) / "association_rules_development.csv")
            self.assertIn("joint_count", output.columns)
            self.assertEqual(output.iloc[0]["evaluation_split"], "Development")
            md = json.loads((Path(tmp) / "rule_evaluation_metadata.json").read_text())
            self.assertEqual(md["development_complete_days"], 4)
            self.assertEqual(md["holdout_complete_days"], 2)
            self.assertFalse(md["development_and_holdout_aggregated"])
            self.assertEqual(len(selected), 3)


if __name__ == "__main__":
    unittest.main()
