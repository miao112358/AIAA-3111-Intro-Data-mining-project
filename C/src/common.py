"""Shared, independently testable calculations for Member C outputs."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import pandas as pd
import pyarrow.compute as pc
import pyarrow.dataset as ds


N_MIN = 10
P_MIN = 2 / 3
MIN_SUPPORT = 0.10
GRID_SIZE_M = 1000
DATA_MODE = "observed_data_only"
DENOMINATOR_TYPE = "observed_days"


def repository_root(start: str | Path | None = None) -> Path:
    """Return the project root containing B/ and preprocessing/."""
    root = Path(start).resolve() if start else Path(__file__).resolve().parents[2]
    if not (root / "B").is_dir() or not (root / "preprocessing").is_dir():
        raise FileNotFoundError(f"Project root is missing B/ or preprocessing/: {root}")
    return root


def results_dir(root: Path) -> Path:
    target = root / "C" / "results"
    target.mkdir(parents=True, exist_ok=True)
    return target


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ValueError(f"Invalid Boolean value: {value!r}")


def load_coverage(root: Path) -> pd.DataFrame:
    path = root / "preprocessing" / "necessary data" / "date_coverage.csv"
    frame = pd.read_csv(path, dtype={"local_date": str})
    required = {
        "local_date", "day_type", "split", "import_complete",
        "AM_status", "MID_status", "PM_status",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"date_coverage.csv missing columns: {sorted(missing)}")
    if frame.local_date.isna().any() or frame.local_date.duplicated().any():
        raise ValueError("Coverage dates must be non-null and unique")
    frame["local_date"] = pd.to_datetime(frame.local_date, errors="raise")
    frame["import_complete"] = frame.import_complete.map(parse_bool)
    if frame.import_complete.isna().any():
        raise ValueError("import_complete contains null values")
    return frame.sort_values("local_date").reset_index(drop=True)


def split_corridor(corridor: str) -> tuple[str, str]:
    parts = str(corridor).split("<->")
    if len(parts) != 2 or not parts[0] or not parts[1] or parts[0] >= parts[1]:
        raise ValueError(f"Invalid canonical corridor: {corridor!r}")
    return parts[0], parts[1]


def direction_from_item(item: str, area_a: str, area_b: str) -> str:
    if ":" not in str(item) or "->" not in str(item):
        raise ValueError(f"Invalid direction item: {item!r}")
    origin, destination = str(item).split(":", 1)[1].split("->", 1)
    if (origin, destination) == (area_a, area_b):
        return "A_TO_B"
    if (origin, destination) == (area_b, area_a):
        return "B_TO_A"
    raise ValueError(f"Item {item!r} does not match {area_a}<->{area_b}")


def opposite(direction: str) -> str:
    if direction == "A_TO_B":
        return "B_TO_A"
    if direction == "B_TO_A":
        return "A_TO_B"
    raise ValueError(f"Invalid direction: {direction!r}")


def classify_direction(ab_count: int, ba_count: int,
                       n_min: int = N_MIN, p_min: float = P_MIN) -> str | None:
    """Apply B's exact total and dominance thresholds."""
    if isinstance(ab_count, bool) or isinstance(ba_count, bool):
        raise ValueError("Counts must be non-negative integers")
    if int(ab_count) != ab_count or int(ba_count) != ba_count or ab_count < 0 or ba_count < 0:
        raise ValueError("Counts must be non-negative integers")
    total = int(ab_count) + int(ba_count)
    if total < n_min or total == 0:
        return None
    dominance = max(ab_count, ba_count) / total
    if ab_count == ba_count or dominance < p_min:
        return None
    return "A_TO_B" if ab_count > ba_count else "B_TO_A"


def load_selected_counts(root: Path, corridors: Iterable[str],
                         windows: Sequence[str] = ("AM", "MID", "PM")) -> pd.DataFrame:
    """Load sparse observed counts for selected 1000m corridors only."""
    corridor_set = {str(value) for value in corridors}
    if not corridor_set:
        return pd.DataFrame(columns=[
            "local_date", "day_type", "split", "window", "area_a", "area_b",
            "corridor", "ab_count", "ba_count",
        ])
    endpoints = sorted({endpoint for corridor in corridor_set for endpoint in split_corridor(corridor)})
    od_path = root / "preprocessing" / "necessary data" / "od_windows.parquet"
    dataset = ds.dataset(str(od_path), format="parquet")
    selected_windows = [str(value).upper() for value in windows]
    filt = (
        (ds.field("grid_size_m") == GRID_SIZE_M)
        & ds.field("window").isin(selected_windows)
        & ds.field("origin_region").isin(endpoints)
        & ds.field("destination_region").isin(endpoints)
    )
    table = dataset.to_table(
        columns=[
            "local_date", "day_type", "split", "window", "origin_region",
            "destination_region", "ride_count",
        ],
        filter=filt,
    )
    for name in table.schema.names:
        if table[name].null_count:
            raise ValueError(f"OD field {name} contains null values")
    if pc.any(pc.less(table["ride_count"], 0)).as_py():
        raise ValueError("ride_count contains negative values")
    frame = table.to_pandas()
    frame["area_a"] = frame[["origin_region", "destination_region"]].min(axis=1)
    frame["area_b"] = frame[["origin_region", "destination_region"]].max(axis=1)
    frame["corridor"] = frame.area_a + "<->" + frame.area_b
    frame = frame[frame.corridor.isin(corridor_set)].copy()
    frame["ab_count"] = frame.ride_count.where(frame.origin_region == frame.area_a, 0).astype("int64")
    frame["ba_count"] = frame.ride_count.where(frame.origin_region == frame.area_b, 0).astype("int64")
    keys = ["local_date", "day_type", "split", "window", "area_a", "area_b", "corridor"]
    grouped = frame.groupby(keys, as_index=False, observed=True)[["ab_count", "ba_count"]].sum()
    grouped["local_date"] = pd.to_datetime(grouped.local_date, errors="raise")
    if grouped.duplicated(["local_date", "window", "corridor"]).any():
        raise AssertionError("Duplicate selected corridor/date/window after aggregation")
    return grouped.sort_values(["corridor", "local_date", "window"]).reset_index(drop=True)


def cohort_dates(coverage: pd.DataFrame, *, day_type: str,
                 start: str | None = None, end: str | None = None,
                 month: int | None = None) -> list[pd.Timestamp]:
    selected = coverage[
        coverage.import_complete
        & coverage.day_type.astype(str).str.lower().eq(str(day_type).lower())
    ].copy()
    if start is not None:
        selected = selected[selected.local_date >= pd.Timestamp(start)]
    if end is not None:
        selected = selected[selected.local_date <= pd.Timestamp(end)]
    if month is not None:
        selected = selected[selected.local_date.dt.month == int(month)]
    return selected.local_date.sort_values().tolist()


def _count_lookup(counts: pd.DataFrame) -> dict[tuple[pd.Timestamp, str, str], tuple[int, int]]:
    return {
        (pd.Timestamp(row.local_date), str(row.corridor), str(row.window)):
            (int(row.ab_count), int(row.ba_count))
        for row in counts.itertuples(index=False)
    }


def calculate_rule_metrics(
    counts: pd.DataFrame,
    dates: Sequence[pd.Timestamp],
    corridor: str,
    antecedent_direction: str,
    first_window: str = "AM",
    second_window: str = "PM",
    *,
    n_min: int = N_MIN,
    p_min: float = P_MIN,
) -> dict[str, object]:
    """Calculate the unified count-backed metrics for one frozen orientation."""
    lookup = _count_lookup(counts[counts.corridor.eq(corridor)])
    consequent_direction = opposite(antecedent_direction)
    n_a = n_p = n_ap = n_eligible = total_rides = 0
    joint_dates: list[str] = []
    for raw_day in dates:
        day = pd.Timestamp(raw_day)
        first = lookup.get((day, corridor, first_window), (0, 0))
        second = lookup.get((day, corridor, second_window), (0, 0))
        total_rides += sum(first) + sum(second)
        first_dir = classify_direction(*first, n_min=n_min, p_min=p_min)
        second_dir = classify_direction(*second, n_min=n_min, p_min=p_min)
        has_a = first_dir == antecedent_direction
        has_p = second_dir == consequent_direction
        n_a += int(has_a)
        n_p += int(has_p)
        if has_a and has_p:
            n_ap += 1
            joint_dates.append(day.strftime("%Y-%m-%d"))
        n_eligible += int(sum(first) >= n_min and sum(second) >= n_min)
    n_obs = len(dates)
    support = n_ap / n_obs if n_obs else None
    confidence = n_ap / n_a if n_a else None
    consequent_rate = n_p / n_obs if n_obs else None
    lift = confidence / consequent_rate if confidence is not None and consequent_rate else None
    conditional = n_ap / n_eligible if n_eligible else None
    return {
        "corridor": corridor,
        "first_window": first_window,
        "second_window": second_window,
        "antecedent_direction": antecedent_direction,
        "consequent_direction": consequent_direction,
        "N_obs": n_obs,
        "N_A": n_a,
        "N_P": n_p,
        "N_AP": n_ap,
        "N_eligible": n_eligible,
        "support": support,
        "confidence": confidence,
        "lift": lift,
        "conditional_reversal_rate": conditional,
        "total_rides": int(total_rides),
        "joint_dates": "|".join(joint_dates),
        "n_min": int(n_min),
        "p_min": float(p_min),
        "denominator_type": DENOMINATOR_TYPE,
        "coverage_status": "unknown",
        "data_mode": DATA_MODE,
    }


def holdout_classification(same_support: float | None, opposite_support: float | None,
                           n_obs: int, min_support: float = MIN_SUPPORT) -> str:
    if not n_obs or same_support is None:
        return "unestimable"
    if same_support >= min_support - 1e-12:
        return "reproduced_same_orientation"
    if opposite_support is not None and opposite_support >= min_support - 1e-12:
        return "direction_flipped"
    return "not_reproduced"


def safe_ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def require_columns(frame: pd.DataFrame, required: Iterable[str], label: str) -> None:
    missing = set(required) - set(frame.columns)
    if missing:
        raise ValueError(f"{label} missing columns: {sorted(missing)}")


def minimum_support_count(n_obs: int, min_support: float = MIN_SUPPORT) -> int:
    return int(math.ceil(float(n_obs) * float(min_support)))
