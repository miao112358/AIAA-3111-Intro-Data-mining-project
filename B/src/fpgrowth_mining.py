"""FP-Growth mining over Development-only daily transactions."""

from __future__ import annotations

import mlxtend
import pandas as pd

try:
    from mlxtend.frequent_patterns import fpgrowth
except ImportError:  # pragma: no cover - dependency is declared in requirements.txt
    fpgrowth = None

from apriori_mining import (
    ITEMSET_COLUMNS,
    AprioriRun,
    _validate_params,
    build_item_metadata,
    encode_transactions,
    filter_reversal_patterns,
)


def run_fpgrowth(transactions, min_support: float, max_len: int = 2) -> pd.DataFrame:
    """Mine frequent itemsets with mlxtend FP-Growth.

    The mapping must contain only complete Development dates. Empty transaction
    rows are kept in the Boolean matrix and therefore remain in the Support
    denominator.
    """
    _validate_params(min_support, max_len)
    encoded = encode_transactions(transactions)
    if not len(encoded.index):
        raise ValueError("At least one complete Development transaction is required")
    if encoded.shape[1] == 0:
        return pd.DataFrame(columns=ITEMSET_COLUMNS)
    if fpgrowth is None:
        raise ImportError("mlxtend is required; install mlxtend to run FP-Growth mining")
    raw = fpgrowth(encoded, min_support=float(min_support), use_colnames=True, max_len=max_len)
    raw["itemset_length"] = raw["itemsets"].map(len)
    raw["dates"] = raw["itemsets"].map(
        lambda itemset: [str(day) for day, row in encoded.iterrows() if all(bool(row[item]) for item in itemset)]
    )
    raw["corridor"] = None
    return raw[ITEMSET_COLUMNS].sort_values(
        ["itemset_length", "support", "itemsets"], ascending=[True, False, True], ignore_index=True
    )


def mine_fpgrowth(
    development_transactions,
    min_support: float,
    max_len: int = 2,
    *,
    spatial_scale=None,
    item_metadata=None,
) -> AprioriRun:
    """Encode, mine and filter reversal patterns using Development data only."""
    encoded = encode_transactions(development_transactions)
    frequent = run_fpgrowth(development_transactions, min_support, max_len)
    metadata_map = item_metadata or {}
    if metadata_map and not frequent.empty:
        def itemset_corridor(itemset):
            item_corridors = {
                (metadata_map[item].get("spatial_scale"), metadata_map[item].get("area_a"),
                 metadata_map[item].get("area_b"))
                for item in itemset if item in metadata_map
            }
            if len(item_corridors) != 1 or len(itemset) != len([item for item in itemset if item in metadata_map]):
                return None
            scale, area_a, area_b = next(iter(item_corridors))
            return {"spatial_scale": scale, "area_a": area_a, "area_b": area_b}
        frequent["corridor"] = frequent["itemsets"].map(itemset_corridor)
    reversals = filter_reversal_patterns(frequent, metadata_map)
    metadata = {
        "algorithm": "FP-Growth",
        "algorithm_version": mlxtend.__version__,
        "min_support": float(min_support),
        "max_len": max_len,
        "development_date_count": len(encoded),
        "empty_transaction_count": int((~encoded.any(axis=1)).sum()) if encoded.shape[1] else len(encoded),
        "spatial_scale": spatial_scale,
        "frequent_itemset_count": len(frequent),
        "reversal_pattern_count": len(reversals),
        "input_partition": "Development",
    }
    return AprioriRun(encoded, frequent, reversals, metadata)


def mine_build_result(build_result, min_support: float, max_len: int = 2) -> AprioriRun:
    """Mine only Transaction Builder's Development partition."""
    transactions = getattr(build_result, "development_transactions", None)
    if transactions is None:
        raise TypeError("Expected a Transaction Builder BuildResult with development_transactions")
    metadata = build_item_metadata(build_result.direction_records)
    return mine_fpgrowth(transactions, min_support, max_len,
                         spatial_scale=build_result.spatial_scale, item_metadata=metadata)


def export_fpgrowth(run: AprioriRun, output_dir) -> None:
    """Write FP-Growth itemsets, reversal patterns and run metadata."""
    from apriori_mining import _write_itemsets
    import json
    from pathlib import Path

    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    _write_itemsets(run.frequent_itemsets, target / "fpgrowth_frequent_itemsets.csv")
    _write_itemsets(run.reversal_patterns, target / "fpgrowth_reversal_patterns.csv")
    (target / "fpgrowth_run_metadata.json").write_text(
        json.dumps(run.metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
