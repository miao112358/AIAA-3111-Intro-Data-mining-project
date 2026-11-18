"""Build daily item transactions from aggregated OD window counts."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from reversal_detection import (
    Direction, DirectionStatus, MIN_DIRECTION_DOMINANCE, MIN_TOTAL_RIDES,
    get_main_direction,
)


@dataclass(frozen=True)
class FieldMapping:
    date: str = "date"
    day_type: str = "day_type"
    time_window: str = "time_window"
    spatial_scale: str = "spatial_scale"
    origin: str = "origin"
    destination: str = "destination"
    ride_count: str = "ride_count"
    is_complete: str = "is_complete"
    record_id: Optional[str] = None


@dataclass(frozen=True)
class BuilderConfig:
    min_total_rides: int = MIN_TOTAL_RIDES
    min_dominance: float = MIN_DIRECTION_DOMINANCE
    windows: tuple[str, ...] = ("AM", "MID", "PM")
    spatial_scale: Optional[str] = None
    missing_od_row_policy: str = "error"
    fields: FieldMapping = field(default_factory=FieldMapping)


@dataclass
class BuildResult:
    """Partitioned build products; ``transactions`` remains a Development alias."""

    development_transactions: dict[str, list[str]]
    holdout_transactions: dict[str, list[str]]
    outside_transactions: dict[str, list[str]]
    item_sources: dict[str, list[dict[str, Any]]]
    direction_records: list[dict[str, Any]]
    transaction_audit: list[dict[str, Any]]
    excluded_dates: list[str]
    spatial_scale: Optional[str]

    @property
    def transactions(self) -> dict[str, list[str]]:
        """Backward-compatible alias; always returns Development only."""
        return self.development_transactions

    @property
    def valid_denominator(self) -> int:
        return len(self.development_transactions)

    @property
    def development_denominator(self) -> int:
        return len(self.development_transactions)

    @property
    def holdout_denominator(self) -> int:
        return len(self.holdout_transactions)

    @property
    def outside_denominator(self) -> int:
        return len(self.outside_transactions)


def _iso_date(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid date: {value!r}") from exc


def _development_split(day: str) -> str:
    return "Development" if "2025-01" <= day[:7] <= "2025-09" else "Holdout" if "2025-10" <= day[:7] <= "2025-12" else "Outside configured study period"


def _parse_complete(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
        raise ValueError(f"is_complete must be a boolean or 'true'/'false', got {value!r}")
    if isinstance(value, int) and value in (0, 1):
        return value == 1
    raise ValueError(f"is_complete must be a boolean or 'true'/'false', got {value!r}")


def _coverage_map(date_coverage: Sequence[Mapping[str, Any]], f: FieldMapping) -> dict[str, bool]:
    coverage: dict[str, bool] = {}
    for row in date_coverage:
        day = _iso_date(row[f.date])
        if day in coverage:
            raise ValueError(f"Duplicate date_coverage record: {day}")
        coverage[day] = _parse_complete(row[f.is_complete])
    return coverage


def build_direction_records(
    od_windows: Sequence[Mapping[str, Any]],
    date_coverage: Sequence[Mapping[str, Any]],
    config: BuilderConfig = BuilderConfig(),
) -> list[dict[str, Any]]:
    """Return corridor/date/window direction decisions, including incomplete dates.

    Missing directional OD rows follow ``missing_od_row_policy``. Explicit
    ``None`` ride counts always remain missing.
    """
    f = config.fields
    coverage = _coverage_map(date_coverage, f)
    if config.missing_od_row_policy not in {"zero", "missing", "error"}:
        raise ValueError("missing_od_row_policy must be 'zero', 'missing', or 'error'")
    scales = {row[f.spatial_scale] for row in od_windows}
    selected_scale = config.spatial_scale
    if selected_scale is None and len(scales) > 1:
        raise ValueError("Input contains multiple spatial scales; select one in BuilderConfig")
    if selected_scale is not None and scales - {selected_scale}:
        raise ValueError("Input contains rows outside the configured spatial scale")

    # Normalize and aggregate duplicate records while preserving explicit missing counts.
    counts: dict[tuple[str, str, str, str, str], Optional[int]] = {}
    source_ids: dict[tuple[str, str, str, str, str], list[Any]] = defaultdict(list)
    day_types: dict[str, set[str]] = defaultdict(set)
    for row in od_windows:
        day = _iso_date(row[f.date])
        if day not in coverage:
            raise ValueError(f"OD date {day} is absent from date_coverage")
        window = str(row[f.time_window]).upper()
        if window not in config.windows:
            raise ValueError(f"Unknown time_window {window!r}; configured windows: {config.windows}")
        scale, origin, destination = str(row[f.spatial_scale]), str(row[f.origin]), str(row[f.destination])
        if origin == destination:
            raise ValueError("Origin and destination must differ")
        count = row[f.ride_count]
        if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 0):
            raise ValueError("ride_count must be a non-negative integer or None")
        key = (day, scale, window, origin, destination)
        if key not in counts:
            counts[key] = count
        elif counts[key] is None or count is None:
            counts[key] = None
        else:
            counts[key] += count
        if row.get(f.day_type) is not None:
            day_types[day].add(str(row[f.day_type]))
        if f.record_id and row.get(f.record_id) is not None:
            # Keep stable input IDs attached to the aggregated directed key.
            source_ids[key].append(row[f.record_id])
    for day, values in day_types.items():
        if len(values) > 1:
            raise ValueError(f"Conflicting day_type values for {day}")

    # Corridor identities come from all observed endpoint pairs, canonically sorted.
    corridor_ids = {(scale, min(o, d), max(o, d)) for (_day, scale, _window, o, d) in counts}
    records = []
    for day in sorted(coverage):
        for scale, area_a, area_b in sorted(corridor_ids):
            for window in config.windows:
                ab_key = (day, scale, window, area_a, area_b)
                ba_key = (day, scale, window, area_b, area_a)
                ab = counts.get(ab_key)
                ba = counts.get(ba_key)
                missing_keys = [key for key, value in ((ab_key, ab), (ba_key, ba)) if key not in counts]
                if missing_keys:
                    if coverage[day] and config.missing_od_row_policy == "error":
                        raise ValueError(
                            f"Missing OD row(s) for {day} {scale} {window} corridor {area_a}<->{area_b}; "
                            "set missing_od_row_policy explicitly after confirming data semantics"
                        )
                    fill = 0 if config.missing_od_row_policy == "zero" else None
                    if ab_key not in counts:
                        ab = fill
                    if ba_key not in counts:
                        ba = fill
                decision = get_main_direction(ab, ba, config.min_total_rides, config.min_dominance)
                records.append({
                    "date": day, "day_type": next(iter(day_types[day]), None), "split": _development_split(day),
                    "time_window": window, "spatial_scale": scale,
                    "area_a": area_a, "area_b": area_b,
                    "ab_count": ab, "ba_count": ba,
                    "aggregation_key_ab": list(ab_key), "aggregation_key_ba": list(ba_key),
                    "source_ids_ab": source_ids.get(ab_key, []), "source_ids_ba": source_ids.get(ba_key, []),
                    "direction": decision.direction.value if decision.direction else None,
                    "dominance": decision.dominance, "status": decision.status.value,
                    "is_complete_date": coverage[day],
                })
    return records


def build_transactions(
    od_windows: Sequence[Mapping[str, Any]],
    date_coverage: Sequence[Mapping[str, Any]],
    config: BuilderConfig = BuilderConfig(),
) -> BuildResult:
    """Build one sorted, duplicate-free list of directional items per complete date."""
    f = config.fields
    coverage_rows = _coverage_map(date_coverage, f)
    records = build_direction_records(od_windows, date_coverage, config)
    partition_transactions = {"Development": {}, "Holdout": {}, "Outside configured study period": {}}
    for day, complete in sorted(coverage_rows.items()):
        if complete:
            partition_transactions[_development_split(day)][day] = []
    sources: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if not record["is_complete_date"] or record["status"] != DirectionStatus.VALID.value:
            continue
        a, b = record["area_a"], record["area_b"]
        direction = Direction(record["direction"])
        origin, destination = (a, b) if direction is Direction.A_TO_B else (b, a)
        item = f'{record["time_window"]}:{origin}->{destination}'
        daily_transactions = partition_transactions[record["split"]]
        if item not in daily_transactions[record["date"]]:
            daily_transactions[record["date"]].append(item)
        sources[item].append({"date": record["date"], "day_type": record["day_type"],
                              "split": record["split"],
                              "time_window": record["time_window"], "spatial_scale": record["spatial_scale"],
                              "origin": origin, "destination": destination,
                              "ab_count": record["ab_count"], "ba_count": record["ba_count"],
                              "dominance": record["dominance"],
                              "aggregation_key_ab": record["aggregation_key_ab"],
                              "aggregation_key_ba": record["aggregation_key_ba"],
                              "source_ids_ab": record["source_ids_ab"],
                              "source_ids_ba": record["source_ids_ba"]})
    for transactions in partition_transactions.values():
        for day in transactions:
            transactions[day].sort()
    excluded = [day for day, complete in sorted(coverage_rows.items()) if not complete]
    audit = [{"date": day, "is_complete": complete, "included_in_denominator": complete,
              "transaction_item_count": len(partition_transactions[_development_split(day)][day]) if complete else None,
              "split": _development_split(day), "reason": "included" if complete else "incomplete date coverage"}
             for day, complete in sorted(coverage_rows.items())]
    scale_set = {r["spatial_scale"] for r in records}
    return BuildResult(partition_transactions["Development"], partition_transactions["Holdout"],
                       partition_transactions["Outside configured study period"], dict(sources), records, audit, excluded,
                       next(iter(scale_set)) if scale_set else config.spatial_scale)


def validate_transactions(result: BuildResult, date_coverage: Sequence[Mapping[str, Any]],
                          config: BuilderConfig = BuilderConfig()) -> list[str]:
    """Return validation errors; an empty list means all transaction invariants pass."""
    errors = []
    f = config.fields
    coverage = _coverage_map(date_coverage, f)
    expected = {split: {day for day, complete in coverage.items() if complete and _development_split(day) == split}
                for split in ("Development", "Holdout", "Outside configured study period")}
    actual = {"Development": result.development_transactions, "Holdout": result.holdout_transactions,
              "Outside configured study period": result.outside_transactions}
    for split in expected:
        if set(actual[split]) != expected[split]:
            errors.append(f"complete {split} dates do not match transaction dates")
    legal_windows = set(config.windows)
    scales = set()
    for day, items in result.transactions.items():
        if len(items) != len(set(items)):
            errors.append(f"duplicate item in transaction {day}")
        for item in items:
            if ":" not in item or "->" not in item:
                errors.append(f"illegal item format: {item}")
                continue
            window, path = item.split(":", 1)
            origin, destination = path.split("->", 1)
            if window not in legal_windows or not origin or not destination or origin == destination:
                errors.append(f"illegal direction item: {item}")
    for record in result.direction_records:
        scales.add(record["spatial_scale"])
        if record["direction"] not in (None, Direction.A_TO_B.value, Direction.B_TO_A.value):
            errors.append(f"illegal direction in record: {record['direction']}")
    if len(scales) > 1:
        errors.append("different spatial scales are mixed")
    return errors


def export_results(result: BuildResult, output_dir: str | Path) -> None:
    """Write transactions JSON and three CSV audit/traceability files."""
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    payload = {"spatial_scale": result.spatial_scale,
               "development": {"valid_denominator": result.development_denominator, "transactions": result.development_transactions},
               "holdout": {"valid_denominator": result.holdout_denominator, "transactions": result.holdout_transactions},
               "outside_study_period": {"valid_denominator": result.outside_denominator, "transactions": result.outside_transactions},
               "item_sources": result.item_sources,
               "excluded_dates": result.excluded_dates}
    (target / "transactions.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_csv(target / "transactions.csv", [
        {"date": day, "items": json.dumps(items, ensure_ascii=False)}
        for day, items in result.development_transactions.items()
    ])
    for split, transactions in (("holdout", result.holdout_transactions),
                                ("outside_study_period", result.outside_transactions)):
        _write_csv(target / f"{split}_transactions.csv", [
            {"date": day, "items": json.dumps(items, ensure_ascii=False)}
            for day, items in transactions.items()
        ])
    _write_csv(target / "direction_records.csv", result.direction_records)
    _write_csv(target / "transaction_audit.csv", result.transaction_audit)


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if fields:
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    simulated_od = [
        {"date": "2025-01-06", "day_type": "weekday", "time_window": "AM", "spatial_scale": "zone",
         "origin": "A", "destination": "B", "ride_count": 18},
        {"date": "2025-01-06", "day_type": "weekday", "time_window": "AM", "spatial_scale": "zone",
         "origin": "B", "destination": "A", "ride_count": 6},
        {"date": "2025-01-06", "day_type": "weekday", "time_window": "PM", "spatial_scale": "zone",
         "origin": "A", "destination": "B", "ride_count": 5},
        {"date": "2025-01-06", "day_type": "weekday", "time_window": "PM", "spatial_scale": "zone",
         "origin": "B", "destination": "A", "ride_count": 15},
    ]
    simulated_coverage = [{"date": "2025-01-06", "is_complete": True},
                          {"date": "2025-01-07", "is_complete": True},
                          {"date": "2025-01-08", "is_complete": False}]
    built = build_transactions(simulated_od, simulated_coverage,
                               BuilderConfig(spatial_scale="zone", missing_od_row_policy="zero"))
    export_results(built, Path(__file__).resolve().parents[1] / "results")
    print(json.dumps({"transactions": built.transactions, "excluded_dates": built.excluded_dates,
                      "valid_denominator": built.valid_denominator,
                      "validation_errors": validate_transactions(built, simulated_coverage)}, indent=2))
