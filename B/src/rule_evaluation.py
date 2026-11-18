"""Association-rule construction and evaluation for directional OD items."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import pandas as pd

from apriori_mining import build_item_metadata


RULE_COLUMNS = [
    "corridor", "spatial_scale", "antecedent", "consequent", "total_complete_days",
    "antecedent_count", "consequent_count", "joint_count", "support", "confidence", "lift",
    "evaluation_split", "status", "antecedent_dates", "consequent_dates", "joint_dates",
]


def _threshold(value: Optional[float], name: str, *, upper: Optional[float] = None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be non-negative or None")
    if upper is not None and value > upper:
        raise ValueError(f"{name} must be at most {upper}")


def _validate_reversed_pair(antecedent: str, consequent: str,
                            item_metadata: Mapping[str, Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    am, pm = item_metadata.get(antecedent), item_metadata.get(consequent)
    if not am or not pm or am.get("window") != "AM" or pm.get("window") != "PM":
        return None
    am_corridor = (am.get("spatial_scale"), am.get("area_a"), am.get("area_b"))
    pm_corridor = (pm.get("spatial_scale"), pm.get("area_a"), pm.get("area_b"))
    if am_corridor != pm_corridor:
        return None
    if am.get("origin") != pm.get("destination") or am.get("destination") != pm.get("origin"):
        return None
    return {"corridor": f"{am['area_a']}<->{am['area_b']}", "spatial_scale": am["spatial_scale"]}


def construct_reversal_rules(
    frequent_itemsets: pd.DataFrame,
    item_metadata: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Construct only AM-to-PM opposite-direction rules from frequent pairs."""
    if "itemsets" not in frequent_itemsets.columns:
        raise ValueError("frequent_itemsets must contain an 'itemsets' column")
    rules = []
    seen = set()
    for itemset in frequent_itemsets["itemsets"]:
        if len(itemset) != 2:
            continue
        for antecedent in itemset:
            for consequent in itemset:
                if antecedent == consequent:
                    continue
                match = _validate_reversed_pair(antecedent, consequent, item_metadata)
                key = (antecedent, consequent)
                if match and key not in seen:
                    seen.add(key)
                    rules.append({"corridor": match["corridor"], "spatial_scale": match["spatial_scale"],
                                  "antecedent": antecedent, "consequent": consequent})
    return rules


def calculate_rule_metrics(
    transactions: Mapping[str, Sequence[str]],
    antecedent: str,
    consequent: str,
    *,
    corridor: Optional[str] = None,
    spatial_scale: Optional[str] = None,
    evaluation_split: str = "Development",
) -> dict[str, Any]:
    """Calculate count-backed Support, Confidence and Lift for one directed rule.

    The supplied transaction mapping is the complete-day denominator, including
    empty transactions. Incomplete dates must be excluded by the caller.
    """
    if not antecedent or not consequent or antecedent == consequent:
        raise ValueError("antecedent and consequent must be distinct non-empty Items")
    total = len(transactions)
    antecedent_dates = sorted(str(day) for day, items in transactions.items() if antecedent in items)
    consequent_dates = sorted(str(day) for day, items in transactions.items() if consequent in items)
    joint_dates = sorted(str(day) for day, items in transactions.items()
                         if antecedent in items and consequent in items)
    ant_count, cons_count, joint_count = len(antecedent_dates), len(consequent_dates), len(joint_dates)
    support = confidence = lift = None
    if total == 0:
        status = "UNESTIMABLE_ZERO_DENOMINATOR"
    else:
        support = joint_count / total
        if ant_count == 0:
            status = "UNESTIMABLE_NO_ANTECEDENT"
        else:
            confidence = joint_count / ant_count
            consequent_support = cons_count / total
            if consequent_support == 0:
                status = "UNESTIMABLE_ZERO_CONSEQUENT_SUPPORT"
            else:
                lift = confidence / consequent_support
                status = "ESTIMATED"
    return {
        "corridor": corridor, "spatial_scale": spatial_scale,
        "antecedent": antecedent, "consequent": consequent,
        "total_complete_days": total, "antecedent_count": ant_count,
        "consequent_count": cons_count, "joint_count": joint_count,
        "support": support, "confidence": confidence, "lift": lift,
        "evaluation_split": evaluation_split, "status": status,
        "antecedent_dates": antecedent_dates, "consequent_dates": consequent_dates,
        "joint_dates": joint_dates,
    }


def _as_frame(rows: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=RULE_COLUMNS)
    for column in ("support", "confidence", "lift"):
        frame[column] = pd.Series([row.get(column) for row in rows], dtype=object)
    return frame


def evaluate_development_rules(
    development_transactions: Mapping[str, Sequence[str]],
    frequent_itemsets: pd.DataFrame,
    item_metadata: Mapping[str, Mapping[str, Any]],
    *,
    min_support: Optional[float] = None,
    min_confidence: Optional[float] = None,
    min_lift: Optional[float] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (all valid reversal rules, threshold-filtered Development rules)."""
    _threshold(min_support, "min_support", upper=1)
    _threshold(min_confidence, "min_confidence", upper=1)
    _threshold(min_lift, "min_lift")
    candidates = construct_reversal_rules(frequent_itemsets, item_metadata)
    rows = [calculate_rule_metrics(development_transactions, rule["antecedent"], rule["consequent"],
                                  corridor=rule["corridor"], spatial_scale=rule["spatial_scale"],
                                  evaluation_split="Development") for rule in candidates]
    complete = _as_frame(rows)
    if complete.empty:
        return complete, complete.copy()
    keep = complete["status"].eq("ESTIMATED")
    if min_support is not None:
        keep &= complete["support"].map(lambda value: value is not None and value >= min_support)
    if min_confidence is not None:
        keep &= complete["confidence"].map(lambda value: value is not None and value >= min_confidence)
    if min_lift is not None:
        keep &= complete["lift"].map(lambda value: value is not None and value >= min_lift)
    return complete, complete.loc[keep].reset_index(drop=True)


def evaluate_build_result(
    build_result: Any,
    frequent_itemsets: pd.DataFrame,
    *,
    min_support: Optional[float] = None,
    min_confidence: Optional[float] = None,
    min_lift: Optional[float] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Evaluate rules using only complete Development transactions from BuildResult."""
    transactions = getattr(build_result, "development_transactions", None)
    if transactions is None:
        raise TypeError("Expected BuildResult with development_transactions")
    metadata = build_item_metadata(build_result.direction_records)
    scales = {value.get("spatial_scale") for value in metadata.values()}
    if len(scales) > 1:
        raise ValueError("Rule evaluation requires one spatial scale per BuildResult")
    return evaluate_development_rules(transactions, frequent_itemsets, metadata,
                                      min_support=min_support, min_confidence=min_confidence,
                                      min_lift=min_lift)


def evaluate_holdout_rules(
    development_rules: pd.DataFrame,
    holdout_transactions: Mapping[str, Sequence[str]],
) -> pd.DataFrame:
    """Evaluate locked Development rule definitions on Holdout dates only.

    No itemset mining or threshold selection occurs here.
    """
    required = {"antecedent", "consequent", "corridor", "spatial_scale", "evaluation_split"}
    if not required.issubset(development_rules.columns):
        raise ValueError(f"development_rules missing columns: {sorted(required - set(development_rules.columns))}")
    rows = []
    for rule in development_rules.to_dict("records"):
        if rule["evaluation_split"] != "Development":
            raise ValueError("Holdout evaluation accepts Development rules only")
        rows.append(calculate_rule_metrics(holdout_transactions, rule["antecedent"], rule["consequent"],
                                           corridor=rule["corridor"], spatial_scale=rule["spatial_scale"],
                                           evaluation_split="Holdout"))
    return _as_frame(rows)


def export_rule_evaluation(
    development_rules: pd.DataFrame,
    holdout_rules: pd.DataFrame,
    output_dir: str | Path,
    *,
    total_development_days: int,
    total_holdout_days: int,
    thresholds: Optional[Mapping[str, Optional[float]]] = None,
) -> None:
    """Export count-backed Development/Holdout rule tables and run metadata."""
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    _export_frame(development_rules, target / "association_rules_development.csv")
    _export_frame(holdout_rules, target / "association_rules_holdout.csv")
    scales = sorted(set(development_rules.get("spatial_scale", pd.Series(dtype=str)).dropna()) |
                    set(holdout_rules.get("spatial_scale", pd.Series(dtype=str)).dropna()))
    metadata = {
        "development_complete_days": total_development_days,
        "holdout_complete_days": total_holdout_days,
        "development_rule_count": len(development_rules),
        "holdout_evaluated_rule_count": len(holdout_rules),
        "spatial_scales": scales,
        "thresholds": dict(thresholds or {}),
        "development_and_holdout_aggregated": False,
        "data_note": "Export intended for simulated data unless supplied by caller; no research conclusion is implied.",
    }
    (target / "rule_evaluation_metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False),
                                                          encoding="utf-8")


def _export_frame(frame: pd.DataFrame, path: Path) -> None:
    output = frame.copy()
    for column in ("antecedent_dates", "consequent_dates", "joint_dates"):
        if column in output:
            output[column] = output[column].map(lambda values: json.dumps(list(values), ensure_ascii=False))
    output.to_csv(path, index=False)
