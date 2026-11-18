"""Shared direction-event pipeline built on the accepted core modules."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from reversal_detection import ReversalStatus, detect_reversal
from transaction_builder import BuilderConfig, BuildResult, build_transactions


def build_direction_events(od_windows: Sequence[Mapping[str, Any]],
                           date_coverage: Sequence[Mapping[str, Any]], *,
                           grid_size_m: int = 1000, config: BuilderConfig | None = None) -> dict[str, Any]:
    """Build scale-specific transactions, direction events and reversal labels.

    Coverage must be explicit True/False. Unknown coverage is rejected by the
    Transaction Builder; missing OD rows retain its configured policy (default
    ``error``). Call once per grid scale.
    """
    if grid_size_m not in (500, 1000):
        raise ValueError("grid_size_m must be 500 or 1000")
    selected = config or BuilderConfig(spatial_scale=f"{grid_size_m}m")
    if selected.spatial_scale is None:
        selected = BuilderConfig(selected.min_total_rides, selected.min_dominance,
                                 selected.windows, f"{grid_size_m}m",
                                 selected.missing_od_row_policy, selected.fields)
    if selected.spatial_scale != f"{grid_size_m}m":
        raise ValueError("BuilderConfig spatial_scale must match grid_size_m")
    result = build_transactions(od_windows, date_coverage, selected)
    by_date = {}
    for row in result.direction_records:
        if row["time_window"] not in ("AM", "PM"):
            continue
        by_date.setdefault(row["date"], {}).setdefault(
            (row["spatial_scale"], row["area_a"], row["area_b"]), {})[row["time_window"]] = row
    events = []
    for day, corridors in sorted(by_date.items()):
        for (scale, area_a, area_b), windows in sorted(corridors.items()):
            am, pm = windows.get("AM"), windows.get("PM")
            if not am or not pm:
                continue
            reversal = detect_reversal(am["ab_count"], am["ba_count"], pm["ab_count"], pm["ba_count"],
                                       selected.min_total_rides, selected.min_dominance)
            events.append({"date": day, "spatial_scale": scale, "area_a": area_a, "area_b": area_b,
                           "am_direction": am["direction"], "pm_direction": pm["direction"],
                           "am_status": am["status"], "pm_status": pm["status"],
                           "reversal_status": reversal.reversal_status.value,
                           "reversal_reason": reversal.reversal_reason,
                           "is_complete_date": am["is_complete_date"] and pm["is_complete_date"]})
    return {"build_result": result, "direction_events": events,
            "development_transactions": result.development_transactions,
            "holdout_transactions": result.holdout_transactions,
            "outside_transactions": result.outside_transactions}
