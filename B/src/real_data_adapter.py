"""Adapter for A's aggregated NYC OD parquet and observed-day coverage files.

The adapter keeps the source's ``unknown`` coverage label intact. Missing
OD keys are represented by absent rows and therefore become zero *observed*
rides only when a requested corridor/date/window is evaluated.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.compute as pc
import pyarrow.dataset as ds


@dataclass
class RealData:
    coverage: pd.DataFrame
    daily_counts: pd.DataFrame
    regions: pd.DataFrame
    source_path: str
    grid_size_m: int


def load_real_data(root: str | Path, grid_size_m: int, *, windows=("AM", "PM")) -> RealData:
    """Read and validate one scale of A's real data without changing sources."""
    if grid_size_m not in (500, 1000):
        raise ValueError("grid_size_m must be 500 or 1000")
    root = Path(root)
    data_dir = root / "preprocessing" / "necessary data"
    od_path = data_dir / "od_windows.parquet"
    cov_path = data_dir / "date_coverage.csv"
    reg_path = data_dir / "regions.parquet"
    for path in (od_path, cov_path, reg_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    coverage = pd.read_csv(cov_path, dtype={"local_date": str})
    required_cov = {"local_date", "day_type", "split", "import_complete", "AM_status", "PM_status"}
    if required_cov - set(coverage.columns):
        raise ValueError(f"Coverage file missing fields: {sorted(required_cov - set(coverage.columns))}")
    if coverage.local_date.isna().any() or coverage.local_date.duplicated().any():
        raise ValueError("Coverage dates must be non-null and unique")
    if coverage.import_complete.isna().any():
        raise ValueError("import_complete contains null values")
    coverage["import_complete"] = coverage.import_complete.map(_parse_bool)
    if coverage[["AM_status", "PM_status"]].isna().any().any():
        raise ValueError("Raw AM/PM coverage statuses contain null values")
    if set(coverage.AM_status) != {"unknown"} or set(coverage.PM_status) != {"unknown"}:
        # Preserve and allow other documented states, but do not relabel them.
        if not set(coverage.AM_status).issubset({"unknown", "complete", "incomplete"}) or not set(coverage.PM_status).issubset({"unknown", "complete", "incomplete"}):
            raise ValueError("Unrecognized raw coverage status values")
    coverage["local_date"] = pd.to_datetime(coverage.local_date, errors="raise").dt.strftime("%Y-%m-%d")
    imported = coverage[coverage.import_complete]
    if imported.empty:
        raise ValueError("No import_complete dates available")

    dataset = ds.dataset(str(od_path), format="parquet")
    expected = {"local_date", "day_type", "split", "window", "grid_size_m", "origin_region", "destination_region", "ride_count"}
    if expected - set(dataset.schema.names):
        raise ValueError(f"OD parquet missing fields: {sorted(expected - set(dataset.schema.names))}")
    selected_windows = tuple(str(w).upper() for w in windows)
    table = dataset.to_table(
        columns=["local_date", "day_type", "split", "window", "origin_region", "destination_region", "ride_count"],
        filter=(ds.field("grid_size_m") == grid_size_m)
        & ds.field("window").isin(list(selected_windows))
        & (ds.field("day_type") == "weekday"),
    )
    if table.num_rows == 0:
        raise ValueError(f"No weekday {selected_windows} OD rows at {grid_size_m}m")
    for field in table.schema.names:
        if table[field].null_count:
            raise ValueError(f"OD field {field} contains {table[field].null_count} null values")
    counts = table["ride_count"]
    if not counts.type.equals(__import__("pyarrow").int64()) and not counts.type.equals(__import__("pyarrow").int32()):
        raise ValueError(f"ride_count must be an integer type, got {counts.type}")
    if pc.any(pc.less(counts, 0)).as_py():
        raise ValueError("ride_count contains negative values")
    imported_dates = set(imported.local_date)
    od_dates = set(table["local_date"].to_pylist())
    extra = od_dates - set(coverage.local_date)
    if extra:
        raise ValueError(f"OD contains dates absent from coverage: {sorted(extra)[:5]}")
    failed = od_dates - imported_dates
    if failed:
        raise ValueError(f"OD has rows on import_complete=False dates: {sorted(failed)[:5]}")

    # The parquet is an already aggregated table: duplicate source keys are an error.
    source_keys = ["local_date", "window", "origin_region", "destination_region"]
    keyed = table.group_by(source_keys).aggregate([("ride_count", "count")])
    duplicate_count = pc.sum(pc.cast(pc.greater(keyed["ride_count_count"], 1), "int64")).as_py() or 0
    if duplicate_count:
        raise ValueError(f"Found {duplicate_count} duplicate OD aggregation keys")

    origin, destination = table["origin_region"], table["destination_region"]
    ab = pc.less_equal(origin, destination)
    table = table.append_column("area_a", pc.if_else(ab, origin, destination))
    table = table.append_column("area_b", pc.if_else(ab, destination, origin))
    table = table.append_column("ab_count", pc.if_else(ab, table["ride_count"], 0))
    table = table.append_column("ba_count", pc.if_else(ab, 0, table["ride_count"]))
    grouped = table.group_by(["local_date", "split", "window", "area_a", "area_b"]).aggregate(
        [("ab_count", "sum"), ("ba_count", "sum")]
    )
    daily = grouped.to_pandas().rename(columns={"local_date": "date", "ab_count_sum": "ab_count", "ba_count_sum": "ba_count"})
    daily["date"] = daily.date.astype(str)
    daily["spatial_scale"] = f"{grid_size_m}m"
    cov_key = coverage.set_index("local_date")
    mismatched = daily.apply(lambda r: str(r["split"]).lower() != str(cov_key.loc[r["date"], "split"]).lower(), axis=1)
    if mismatched.any():
        raise ValueError("OD split disagrees with date_coverage split")

    regions = __import__("pyarrow.parquet", fromlist=["read_table"]).read_table(str(reg_path)).to_pandas()
    region_set = set(regions.loc[regions.grid_size_m == grid_size_m, "region_id"].astype(str))
    od_regions = set(daily.area_a.astype(str)) | set(daily.area_b.astype(str))
    orphan = od_regions - region_set
    if orphan:
        raise ValueError(f"OD contains {len(orphan)} region IDs absent from {grid_size_m}m regions")
    return RealData(coverage, daily, regions, str(od_path), grid_size_m)


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ValueError(f"Invalid import_complete value: {value!r}")
