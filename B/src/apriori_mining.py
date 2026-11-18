"""Apriori mining for Development-only daily transactions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import pandas as pd

try:
    from mlxtend.frequent_patterns import apriori
except ImportError:  # Keep encoding/metadata helpers importable without the optional runtime dependency.
    apriori = None


ITEMSET_COLUMNS = ["itemsets", "support", "itemset_length", "dates", "corridor"]


@dataclass
class AprioriRun:
    encoded: pd.DataFrame
    frequent_itemsets: pd.DataFrame
    reversal_patterns: pd.DataFrame
    metadata: dict[str, Any]


def encode_transactions(transactions: Mapping[str, Sequence[str]]) -> pd.DataFrame:
    """Encode date-keyed Development transactions as a Boolean DataFrame.

    All date rows are retained, including transactions with no items.
    """
    dates = sorted(str(day) for day in transactions)
    if len(dates) != len(set(dates)):
        raise ValueError("Transaction dates must be unique after string normalization")
    item_set: set[str] = set()
    normalized: dict[str, set[str]] = {}
    for day in dates:
        items = list(transactions[day])
        if any(not isinstance(item, str) or not item for item in items):
            raise ValueError(f"Transaction {day} contains an invalid item")
        if len(items) != len(set(items)):
            raise ValueError(f"Transaction {day} contains duplicate items")
        normalized[day] = set(items)
        item_set.update(items)
    columns = sorted(item_set)
    frame = pd.DataFrame(False, index=pd.Index(dates, name="date"), columns=columns, dtype=bool)
    for day, items in normalized.items():
        if items:
            frame.loc[day, list(items)] = True
    return frame.astype(bool)


def _validate_params(min_support: float, max_len: int) -> None:
    if isinstance(min_support, bool) or not isinstance(min_support, (int, float)) or not 0 < min_support <= 1:
        raise ValueError("min_support must be greater than 0 and at most 1")
    if isinstance(max_len, bool) or not isinstance(max_len, int) or max_len < 1:
        raise ValueError("max_len must be a positive integer")


def run_apriori(
    transactions: Mapping[str, Sequence[str]],
    min_support: float,
    max_len: int = 2,
) -> pd.DataFrame:
    """Mine frequent itemsets from date-keyed Development transactions.

    Support uses every supplied complete Development date, including empty rows.
    The return value has ``itemsets``, ``support``, ``itemset_length``, ``dates``
    and metadata-derived ``corridor`` columns. When no metadata is supplied,
    ``corridor`` is null and date traceability is still retained.
    """
    _validate_params(min_support, max_len)
    encoded = encode_transactions(transactions)
    if len(encoded.index) == 0:
        raise ValueError("At least one complete Development transaction is required")
    if encoded.shape[1] == 0:
        return pd.DataFrame(columns=ITEMSET_COLUMNS)
    if apriori is None:
        raise ImportError("mlxtend is required; install mlxtend to run Apriori mining")
    raw = apriori(encoded, min_support=float(min_support), use_colnames=True, max_len=max_len)
    raw["itemset_length"] = raw["itemsets"].map(len)
    raw["dates"] = raw["itemsets"].map(
        lambda itemset: [str(day) for day, row in encoded.iterrows() if all(bool(row[item]) for item in itemset)]
    )
    raw["corridor"] = None
    return raw[ITEMSET_COLUMNS].sort_values(
        ["itemset_length", "support", "itemsets"], ascending=[True, False, True], ignore_index=True
    )


def build_item_metadata(direction_records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build exact item-to-corridor metadata from Transaction Builder records."""
    metadata: dict[str, dict[str, Any]] = {}
    for record in direction_records:
        if record.get("status") != "VALID" or not record.get("direction"):
            continue
        window = str(record["time_window"]).upper()
        a, b = str(record["area_a"]), str(record["area_b"])
        if record["direction"] == "A_TO_B":
            origin, destination = a, b
        elif record["direction"] == "B_TO_A":
            origin, destination = b, a
        else:
            continue
        item = f"{window}:{origin}->{destination}"
        value = {"window": window, "spatial_scale": str(record["spatial_scale"]),
                 "area_a": a, "area_b": b, "origin": origin, "destination": destination}
        existing = metadata.get(item)
        if existing is not None and existing != value:
            raise ValueError(f"Item {item!r} maps to conflicting corridor metadata; mine one spatial scale at a time")
        metadata[item] = value
    return metadata


def filter_reversal_patterns(
    frequent_itemsets: pd.DataFrame,
    item_metadata: Mapping[str, Mapping[str, Any]],
) -> pd.DataFrame:
    """Keep only AM/PM pairs with opposite directions on the same corridor."""
    rows = []
    for _, row in frequent_itemsets.iterrows():
        itemset = frozenset(row["itemsets"])
        if len(itemset) != 2:
            continue
        items = list(itemset)
        left, right = (item_metadata.get(item, {}) for item in items)
        if not left or not right:
            continue
        if {left.get("window"), right.get("window")} != {"AM", "PM"}:
            continue
        left_corridor = (left.get("spatial_scale"), left.get("area_a"), left.get("area_b"))
        right_corridor = (right.get("spatial_scale"), right.get("area_a"), right.get("area_b"))
        if left_corridor != right_corridor:
            continue
        if left.get("origin") != right.get("destination") or left.get("destination") != right.get("origin"):
            continue
        ordered_items = sorted(items, key=lambda item: (item_metadata[item]["window"], item))
        rows.append({"itemsets": frozenset(ordered_items), "support": row["support"],
                     "itemset_length": 2, "dates": row.get("dates", []),
                     "corridor": {"spatial_scale": left_corridor[0], "area_a": left_corridor[1],
                                  "area_b": left_corridor[2]}})
    return pd.DataFrame(rows, columns=ITEMSET_COLUMNS)


def mine_development(
    development_transactions: Mapping[str, Sequence[str]],
    min_support: float,
    max_len: int = 2,
    *,
    spatial_scale: Optional[str] = None,
    item_metadata: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> AprioriRun:
    """Run encoding, Apriori and reversal filtering on Development data only."""
    encoded = encode_transactions(development_transactions)
    frequent = run_apriori(development_transactions, min_support, max_len)
    metadata_map = item_metadata or {}
    if metadata_map and not frequent.empty:
        def itemset_corridor(itemset: frozenset[str]) -> Optional[dict[str, str]]:
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
        "min_support": float(min_support), "max_len": max_len,
        "development_date_count": len(encoded),
        "empty_transaction_count": int((~encoded.any(axis=1)).sum()) if encoded.shape[1] else len(encoded),
        "spatial_scale": spatial_scale,
        "frequent_itemset_count": len(frequent), "reversal_pattern_count": len(reversals),
        "input_partition": "Development",
    }
    return AprioriRun(encoded, frequent, reversals, metadata)


def mine_build_result(build_result: Any, min_support: float, max_len: int = 2) -> AprioriRun:
    """Mine only ``BuildResult.development_transactions`` and its direction records."""
    transactions = getattr(build_result, "development_transactions", None)
    if transactions is None:
        raise TypeError("Expected a Transaction Builder BuildResult with development_transactions")
    metadata = build_item_metadata(build_result.direction_records)
    return mine_development(transactions, min_support, max_len,
                            spatial_scale=build_result.spatial_scale, item_metadata=metadata)


def export_apriori(run: AprioriRun, output_dir: str | Path) -> None:
    """Export frequent itemsets, reversal-only patterns, and run metadata."""
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    _write_itemsets(run.frequent_itemsets, target / "apriori_frequent_itemsets.csv")
    _write_itemsets(run.reversal_patterns, target / "apriori_reversal_patterns.csv")
    (target / "apriori_run_metadata.json").write_text(
        json.dumps(run.metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _write_itemsets(frame: pd.DataFrame, path: Path) -> None:
    output = frame.copy()
    if "itemsets" in output:
        output["itemsets"] = output["itemsets"].map(lambda x: json.dumps(sorted(x), ensure_ascii=False))
    if "dates" in output:
        output["dates"] = output["dates"].map(lambda x: json.dumps(list(x), ensure_ascii=False))
    if "corridor" in output:
        output["corridor"] = output["corridor"].map(lambda x: json.dumps(x, ensure_ascii=False) if x else "")
    output.to_csv(path, index=False)
