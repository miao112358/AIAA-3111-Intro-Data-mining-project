"""Shared Apriori/FP-Growth and association-rule orchestration."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from apriori_mining import mine_development as mine_apriori, build_item_metadata
from fpgrowth_mining import mine_fpgrowth
from rule_evaluation import evaluate_development_rules, evaluate_holdout_rules


def mine_rules(build_result: Any, *, algorithm: str = "apriori", min_support: float = 0.10,
               max_len: int = 2, min_confidence: Optional[float] = None,
               min_lift: Optional[float] = None) -> dict[str, Any]:
    """Mine and score reversal rules from Development; evaluate frozen rules on Holdout."""
    transactions = getattr(build_result, "development_transactions", None)
    if transactions is None:
        raise TypeError("build_result must provide development_transactions")
    metadata = build_item_metadata(build_result.direction_records)
    scales = {entry["spatial_scale"] for entry in metadata.values()}
    if len(scales) > 1:
        raise ValueError("Run one spatial scale at a time")
    scale = next(iter(scales), build_result.spatial_scale)
    if algorithm == "apriori":
        mined = mine_apriori(transactions, min_support, max_len, spatial_scale=scale, item_metadata=metadata)
    elif algorithm == "fpgrowth":
        mined = mine_fpgrowth(transactions, min_support, max_len, spatial_scale=scale, item_metadata=metadata)
    else:
        raise ValueError("algorithm must be 'apriori' or 'fpgrowth'")
    full_rules, selected_rules = evaluate_development_rules(
        transactions, mined.frequent_itemsets, metadata, min_support=min_support,
        min_confidence=min_confidence, min_lift=min_lift)
    holdout_rules = evaluate_holdout_rules(full_rules, build_result.holdout_transactions)
    return {"mining_result": mined, "development_rules": full_rules,
            "selected_development_rules": selected_rules, "holdout_evaluation": holdout_rules}
