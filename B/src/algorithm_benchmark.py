"""Fair, Development-only Apriori versus FP-Growth benchmark utilities."""

from __future__ import annotations

import json
import multiprocessing as mp
import platform
import random
import resource
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import mlxtend
import numpy as np
import pandas as pd

from apriori_mining import build_item_metadata, encode_transactions, filter_reversal_patterns
from mlxtend.frequent_patterns import apriori, fpgrowth


def generate_simulated_transactions(
    transaction_count: int,
    item_count: int = 24,
    average_length: float = 6.0,
    item_density: Optional[float] = None,
    seed: int = 3111,
) -> tuple[dict[str, list[str]], dict[str, dict[str, Any]]]:
    """Generate deterministic synthetic Development transactions and item metadata."""
    if isinstance(transaction_count, bool) or not isinstance(transaction_count, int) or transaction_count < 1:
        raise ValueError("transaction_count must be a positive integer")
    if isinstance(item_count, bool) or not isinstance(item_count, int) or item_count < 1:
        raise ValueError("item_count must be a positive integer")
    if not isinstance(average_length, (int, float)) or not 0 <= average_length <= item_count:
        raise ValueError("average_length must be between 0 and item_count")
    if item_density is None:
        item_density = average_length / item_count
    if isinstance(item_density, bool) or not isinstance(item_density, (int, float)) or not 0 <= item_density <= 1:
        raise ValueError("item_density must be between 0 and 1")
    expected_density = average_length / item_count
    if abs(float(item_density) - expected_density) > 1e-12:
        raise ValueError("item_density must equal average_length / item_count so both parameters describe the same target density")
    rng = random.Random(seed)
    items = []
    metadata = {}
    for index in range(item_count):
        corridor_index = index // 2
        a, b = f"Z{corridor_index:02d}A", f"Z{corridor_index:02d}B"
        if index % 2 == 0:
            window, origin, destination = "AM", a, b
        else:
            window, origin, destination = "PM", b, a
        item = f"{window}:{origin}->{destination}"
        items.append(item)
        metadata[item] = {"window": window, "spatial_scale": "simulated", "area_a": a,
                          "area_b": b, "origin": origin, "destination": destination}
    transactions = {}
    for day_index in range(transaction_count):
        size = min(item_count, max(0, round(rng.gauss(average_length, max(0.5, average_length * 0.2)))))
        selected = rng.sample(items, size)
        transactions[f"2025-01-{(day_index % 28) + 1:02d}-{day_index // 28:04d}"] = selected
    return transactions, metadata


def _raw_mine(algorithm: str, encoded: pd.DataFrame, min_support: float, max_len: int) -> pd.DataFrame:
    miner = apriori if algorithm == "Apriori" else fpgrowth
    return miner(encoded, min_support=min_support, use_colnames=True, max_len=max_len)


def _mine_encoded(algorithm: str, encoded: pd.DataFrame, min_support: float, max_len: int) -> pd.DataFrame:
    raw = _raw_mine(algorithm, encoded, min_support, max_len)
    raw["itemset_length"] = raw["itemsets"].map(len)
    raw["dates"] = raw["itemsets"].map(
        lambda itemset: [str(day) for day, row in encoded.iterrows() if all(bool(row[item]) for item in itemset)]
    )
    raw["corridor"] = None
    return raw


def _decorate_and_filter(frequent: pd.DataFrame, metadata: Mapping[str, Mapping[str, Any]]) -> pd.DataFrame:
    if metadata and not frequent.empty:
        def corridor(itemset):
            values = {(metadata[item]["spatial_scale"], metadata[item]["area_a"], metadata[item]["area_b"])
                      for item in itemset if item in metadata}
            if len(values) != 1 or any(item not in metadata for item in itemset):
                return None
            scale, area_a, area_b = next(iter(values))
            return {"spatial_scale": scale, "area_a": area_a, "area_b": area_b}
        frequent["corridor"] = frequent["itemsets"].map(corridor)
    return filter_reversal_patterns(frequent, metadata)


def _standardized(frame: pd.DataFrame, digits: int = 12) -> dict[tuple[str, ...], float]:
    return {tuple(sorted(itemset)): round(float(support), digits)
            for itemset, support in zip(frame["itemsets"], frame["support"])}


def _worker_peak_rss(algorithm: str, transactions, min_support: float, max_len: int, queue) -> None:
    try:
        encoded = encode_transactions(transactions)
        _mine_encoded(algorithm, encoded, min_support, max_len)
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_bytes = int(peak if sys.platform == "darwin" else peak * 1024)
        queue.put((peak_bytes, None))
    except Exception as exc:  # propagate subprocess failures to the caller
        queue.put((None, f"{type(exc).__name__}: {exc}"))


def _measure_peak_rss(algorithm: str, transactions, min_support: float, max_len: int) -> int:
    context = mp.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_worker_peak_rss,
                              args=(algorithm, transactions, min_support, max_len, queue))
    process.start()
    process.join()
    if process.exitcode != 0:
        raise RuntimeError(f"{algorithm} RSS worker exited with code {process.exitcode}")
    peak_bytes, error = queue.get(timeout=5)
    if error:
        raise RuntimeError(f"{algorithm} RSS worker failed: {error}")
    return peak_bytes


def benchmark_transactions(
    transactions: Mapping[str, Sequence[str]],
    min_support: float,
    max_len: int = 2,
    repeat_count: int = 5,
    *,
    item_metadata: Optional[Mapping[str, Mapping[str, Any]]] = None,
    spatial_scale: Optional[str] = None,
    consistency_tolerance: float = 1e-12,
) -> list[dict[str, Any]]:
    """Benchmark both miners on one fixed Development transaction mapping."""
    if not 0 < min_support <= 1:
        raise ValueError("min_support must be in (0, 1]")
    if not isinstance(max_len, int) or isinstance(max_len, bool) or max_len < 1:
        raise ValueError("max_len must be a positive integer")
    if not isinstance(repeat_count, int) or isinstance(repeat_count, bool) or repeat_count < 5:
        raise ValueError("repeat_count must be at least 5")
    if consistency_tolerance < 0:
        raise ValueError("consistency_tolerance must be non-negative")
    metadata = item_metadata or {}
    encoded = encode_transactions(transactions)
    if not len(encoded):
        raise ValueError("At least one complete Development transaction is required")

    # One untimed warm-up per implementation.
    for name in ("Apriori", "FP-Growth"):
        _mine_encoded(name, encoded, min_support, max_len)

    timings = {name: {"encoding": [], "mining": [], "end_to_end": []}
               for name in ("Apriori", "FP-Growth")}
    outputs = {name: [] for name in timings}
    order = ("Apriori", "FP-Growth")
    for repetition in range(repeat_count):
        for name in (order if repetition % 2 == 0 else tuple(reversed(order))):
            start_all = time.perf_counter()
            start_encode = time.perf_counter()
            run_encoded = encode_transactions(transactions)
            encode_elapsed = time.perf_counter() - start_encode
            start_mining = time.perf_counter()
            raw = _raw_mine(name, run_encoded, min_support, max_len)
            mining_elapsed = time.perf_counter() - start_mining
            frequent = raw
            frequent["itemset_length"] = frequent["itemsets"].map(len)
            frequent["dates"] = frequent["itemsets"].map(
                lambda itemset: [str(day) for day, row in run_encoded.iterrows()
                                 if all(bool(row[item]) for item in itemset)]
            )
            frequent["corridor"] = None
            _decorate_and_filter(frequent, metadata)
            timings[name]["encoding"].append(encode_elapsed)
            timings[name]["mining"].append(mining_elapsed)
            timings[name]["end_to_end"].append(time.perf_counter() - start_all)
            outputs[name].append(frequent)

    reference = outputs["Apriori"][-1]
    reference_map = _standardized(reference)
    reference_reversals = _standardized(_decorate_and_filter(reference.copy(), metadata))
    consistent = True
    for name in ("Apriori", "FP-Growth"):
        for output in outputs[name]:
            candidate = _standardized(output)
            if candidate.keys() != reference_map.keys() or any(
                abs(candidate[key] - reference_map[key]) > consistency_tolerance for key in reference_map
            ):
                consistent = False
        reversal = _standardized(_decorate_and_filter(outputs[name][-1].copy(), metadata))
        if reversal.keys() != reference_reversals.keys() or any(
            abs(reversal[key] - reference_reversals[key]) > consistency_tolerance for key in reference_reversals
        ):
            consistent = False

    if not consistent:
        raise RuntimeError("Apriori and FP-Growth outputs differ; benchmark results are invalid and will not be returned or exported")

    rows = []
    for name in ("Apriori", "FP-Growth"):
        peaks = _measure_peak_rss(name, transactions, min_support, max_len)
        final = outputs[name][-1]
        reversal = _decorate_and_filter(final.copy(), metadata)
        row = {
            "dataset_size": len(transactions), "item_count": encoded.shape[1],
            "average_transaction_length": float(encoded.sum(axis=1).mean()),
            "min_support": float(min_support), "max_len": max_len, "algorithm": name,
            "encoding_time": statistics.median(timings[name]["encoding"]),
            "encoding_time_std": statistics.stdev(timings[name]["encoding"]),
            "mining_time": statistics.median(timings[name]["mining"]),
            "mining_time_std": statistics.stdev(timings[name]["mining"]),
            "end_to_end_time": statistics.median(timings[name]["end_to_end"]),
            "end_to_end_time_std": statistics.stdev(timings[name]["end_to_end"]),
            "peak_memory": peaks, "peak_memory_unit": "bytes",
            "peak_memory_method": "resource.getrusage(RUSAGE_SELF).ru_maxrss in spawned process",
            "peak_memory_includes_imports": True,
            "frequent_itemset_count": len(final), "reversal_pattern_count": len(reversal),
            "support_consistency": consistent, "result_consistent": consistent,
            "repeat_count": repeat_count,
            "runtime_statistic": "median and sample standard deviation; seconds",
            "development_date_count": len(transactions), "spatial_scale": spatial_scale,
        }
        rows.append(row)
    return rows


def benchmark_build_result(build_result, min_support: float, max_len: int = 2,
                           repeat_count: int = 5) -> list[dict[str, Any]]:
    """Benchmark only ``development_transactions`` from a Transaction Builder result."""
    transactions = getattr(build_result, "development_transactions", None)
    if transactions is None:
        raise TypeError("Expected BuildResult with development_transactions")
    metadata = build_item_metadata(build_result.direction_records)
    return benchmark_transactions(transactions, min_support, max_len, repeat_count,
                                  item_metadata=metadata, spatial_scale=build_result.spatial_scale)


def export_benchmark(rows: Sequence[Mapping[str, Any]], output_dir: str | Path) -> None:
    """Export benchmark CSV and JSON with runtime environment metadata."""
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(target / "algorithm_benchmark.csv", index=False)
    payload = {
        "environment": {"python": sys.version, "pandas": pd.__version__, "mlxtend": mlxtend.__version__,
                        "numpy": np.__version__, "platform": platform.platform()},
        "measurement_notes": {
            "timing": "one warm-up, alternating order, repeated measurements; median and sample standard deviation",
            "memory": "peak RSS in bytes from independent spawned processes; includes Python and algorithm-library import overhead",
            "synthetic_data_only": True,
        },
        "runs": list(rows),
    }
    (target / "algorithm_benchmark.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_scale_experiments(
    sizes=(500, 2000, 5000), *, item_count: int = 24, average_length: float = 6,
    item_density: Optional[float] = None, min_support: float = 0.05, max_len: int = 2,
    repeat_count: int = 5, seed: int = 3111,
) -> list[dict[str, Any]]:
    """Run deterministic synthetic scale experiments (not research results)."""
    all_rows = []
    for size in sizes:
        transactions, metadata = generate_simulated_transactions(
            size, item_count, average_length, item_density, seed
        )
        all_rows.extend(benchmark_transactions(transactions, min_support, max_len, repeat_count,
                                               item_metadata=metadata, spatial_scale="simulated"))
    return all_rows


if __name__ == "__main__":
    results_dir = Path(__file__).resolve().parents[1] / "results"
    benchmark_rows = run_scale_experiments(repeat_count=5)
    export_benchmark(benchmark_rows, results_dir)
    print(json.dumps({
        "synthetic_only": True,
        "all_results_consistent": all(row["result_consistent"] for row in benchmark_rows),
        "runs": benchmark_rows,
        "results_dir": str(results_dir),
    }, indent=2))
