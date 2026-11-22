from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds


E4C_DIR = Path(__file__).resolve().parent
PROJECT_DIR = E4C_DIR.parent
B_SOURCE_DIR = PROJECT_DIR / "AIAA-3111-Intro-Data-mining-project" / "E4c_B_handoff" / "B" / "src"
if str(B_SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(B_SOURCE_DIR))

from reversal_detection import get_main_direction


def default_data_dir() -> Path:
    processed = PROJECT_DIR / "data" / "processed"
    candidates = []
    for path in processed.iterdir() if processed.is_dir() else []:
        status_path = path / "run_status.json"
        if not path.is_dir() or not status_path.is_file():
            continue
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if status.get("status") == "complete" and all(
            (path / name).is_file()
            for name in ("od_windows.parquet", "date_coverage.csv", "regions.parquet")
        ):
            candidates.append(path)
    if not candidates:
        raise FileNotFoundError(f"No complete preprocessing directory found under {processed}")
    return sorted(candidates, key=lambda p: p.name)[-1]


def resolve_data_dir(value: str | None) -> Path:
    path = Path(value).expanduser().resolve() if value else default_data_dir()
    required = ("od_windows.parquet", "date_coverage.csv", "regions.parquet")
    missing = [name for name in required if not (path / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing {', '.join(missing)} in {path}")
    return path


def load_parameters(path: str | Path | None = None) -> dict:
    config_path = Path(path).expanduser() if path else E4C_DIR / "parameters.json"
    values = json.loads(config_path.read_text(encoding="utf-8"))
    required = {"n_min", "p_min", "min_support", "top_k", "day_type", "windows"}
    missing = required - values.keys()
    if missing:
        raise ValueError(f"Parameter file is missing: {sorted(missing)}")
    if not isinstance(values["n_min"], int) or values["n_min"] < 0:
        raise ValueError("n_min must be a non-negative integer")
    if not 0 <= float(values["p_min"]) <= 1 or not 0 <= float(values["min_support"]) <= 1:
        raise ValueError("p_min and min_support must be between 0 and 1")
    if not isinstance(values["top_k"], int) or values["top_k"] < 1:
        raise ValueError("top_k must be a positive integer")
    if [str(x).upper() for x in values["windows"]] != ["AM", "PM"]:
        raise ValueError("E4c requires windows [AM, PM]")
    if str(values["day_type"]).lower() != "weekday" or values.get("period") != "full_year":
        raise ValueError("E4c parameters must specify full-year weekdays")
    return values


def _parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ValueError(f"Invalid import_complete value: {value!r}")


def load_scale(data_dir: str | Path, grid_size_m: int):
    if grid_size_m not in (500, 1000):
        raise ValueError("grid_size_m must be 500 or 1000")
    data_dir = Path(data_dir)
    coverage = pd.read_csv(data_dir / "date_coverage.csv", dtype={"local_date": str})
    cov_required = {"local_date", "day_type", "split", "import_complete", "AM_status", "PM_status"}
    if cov_required - set(coverage.columns):
        raise ValueError(f"Coverage file missing: {sorted(cov_required - set(coverage.columns))}")
    if coverage.local_date.isna().any() or coverage.local_date.duplicated().any():
        raise ValueError("Coverage dates must be non-null and unique")
    coverage["import_complete"] = coverage.import_complete.map(_parse_bool)
    coverage["local_date"] = pd.to_datetime(coverage.local_date, errors="raise").dt.strftime("%Y-%m-%d")
    for col in ("AM_status", "PM_status"):
        if coverage[col].isna().any() or not set(coverage[col]).issubset({"unknown", "complete", "incomplete"}):
            raise ValueError(f"Invalid values in {col}")
    dates = sorted(
        coverage.loc[
            coverage.import_complete
            & coverage.day_type.astype(str).str.lower().eq("weekday"),
            "local_date",
        ].tolist()
    )
    if not dates:
        raise ValueError("No imported weekdays in date_coverage.csv")

    dataset = ds.dataset(str(data_dir / "od_windows.parquet"), format="parquet")
    required_od = {
        "local_date", "day_type", "split", "window", "grid_size_m",
        "origin_region", "destination_region", "ride_count",
    }
    if required_od - set(dataset.schema.names):
        raise ValueError(f"OD file missing: {sorted(required_od - set(dataset.schema.names))}")
    base_filter = (
        (ds.field("grid_size_m") == grid_size_m)
        & ds.field("window").isin(["AM", "PM"])
        & (ds.field("day_type") == "weekday")
    )
    available_dates = set(dataset.to_table(columns=["local_date"], filter=base_filter)["local_date"].to_pylist())
    known_dates = set(coverage.local_date)
    if available_dates - known_dates:
        raise ValueError(f"OD dates absent from coverage: {sorted(available_dates - known_dates)[:5]}")
    imported = set(coverage.loc[coverage.import_complete, "local_date"])
    failed_dates = available_dates - imported
    if failed_dates:
        raise ValueError(f"OD rows exist on import_complete=False dates: {sorted(failed_dates)[:5]}")
    date_filter = ds.field("local_date").isin(dates)
    table = dataset.to_table(
        columns=["local_date", "day_type", "split", "window", "origin_region", "destination_region", "ride_count"],
        filter=base_filter & date_filter,
    )
    if table.num_rows == 0:
        raise ValueError(f"No imported weekday AM/PM rows at {grid_size_m}m")
    for name in table.schema.names:
        if table[name].null_count:
            raise ValueError(f"OD field {name} contains nulls")
    if not (pa.types.is_integer(table.schema.field("ride_count").type)):
        raise ValueError("ride_count must be an integer")
    if pc.any(pc.less(table["ride_count"], 0)).as_py():
        raise ValueError("ride_count contains negative values")
    keys = ["local_date", "window", "origin_region", "destination_region"]
    grouped_keys = table.group_by(keys).aggregate([("ride_count", "count")])
    duplicate_count = pc.sum(pc.cast(pc.greater(grouped_keys["ride_count_count"], 1), pa.int64())).as_py() or 0
    if duplicate_count:
        raise ValueError(f"Found {duplicate_count} duplicate OD aggregation keys")

    coverage_by_date = coverage.set_index("local_date")
    split_rows = table.select(["local_date", "split"]).to_pandas().drop_duplicates()
    for row in split_rows.itertuples(index=False):
        if str(row.split).lower() != str(coverage_by_date.loc[row.local_date, "split"]).lower():
            raise ValueError(f"OD split disagrees with coverage on {row.local_date}")

    regions = pd.read_parquet(data_dir / "regions.parquet")
    region_required = {"grid_size_m", "region_id", "parent_region"}
    if region_required - set(regions.columns):
        raise ValueError(f"Regions file missing: {sorted(region_required - set(regions.columns))}")
    selected_regions = regions[regions.grid_size_m == grid_size_m].copy()
    if selected_regions.region_id.isna().any() or selected_regions.region_id.duplicated().any():
        raise ValueError(f"Region IDs must be present and unique at {grid_size_m}m")
    valid_ids = set(selected_regions.region_id.astype(str))
    observed_ids = set(pc.unique(table["origin_region"]).to_pylist()) | set(pc.unique(table["destination_region"]).to_pylist())
    if observed_ids - valid_ids:
        raise ValueError(f"OD has region IDs missing from {grid_size_m}m regions")
    parent_map = None
    if grid_size_m == 500:
        parent_map = dict(zip(selected_regions.region_id.astype(str), selected_regions.parent_region.astype(str)))
        if selected_regions.parent_region.isna().any():
            raise ValueError("500m regions contain missing parent_region values")
        parent_ids = set(regions.loc[regions.grid_size_m == 1000, "region_id"].astype(str))
        if set(parent_map.values()) - parent_ids:
            raise ValueError("500m parent_region refers to an unknown 1000m region")
    return coverage, dates, table, regions, parent_map


def daily_corridors(table, grid_size_m: int) -> pd.DataFrame:
    origin, destination = table["origin_region"], table["destination_region"]
    ab_first = pc.less_equal(origin, destination)
    table = table.append_column("area_a", pc.if_else(ab_first, origin, destination))
    table = table.append_column("area_b", pc.if_else(ab_first, destination, origin))
    table = table.append_column("ab_count", pc.if_else(ab_first, table["ride_count"], 0))
    table = table.append_column("ba_count", pc.if_else(ab_first, 0, table["ride_count"]))
    grouped = table.group_by(["local_date", "split", "window", "area_a", "area_b"]).aggregate(
        [("ab_count", "sum"), ("ba_count", "sum")]
    )
    frame = grouped.to_pandas().rename(
        columns={"local_date": "date", "ab_count_sum": "ab_count", "ba_count_sum": "ba_count"}
    )
    frame["date"] = frame.date.astype(str)
    frame["spatial_scale"] = f"{grid_size_m}m"
    return frame


def _direction(counts, params):
    result = get_main_direction(
        int(counts[0]), int(counts[1]), int(params["n_min"]), float(params["p_min"])
    )
    direction = None
    if result.direction is not None:
        direction = "->".join(("A", "B") if result.direction.value == "A_TO_B" else ("B", "A"))
    return direction, result.dominance, result.status.value


def _actual_direction(pair, direction):
    if direction is None:
        return None
    left, right = direction.split("->")
    return f"{pair[0]}->{pair[1]}" if (left, right) == ("A", "B") else f"{pair[1]}->{pair[0]}"


def analyze_scale(daily: pd.DataFrame, coverage: pd.DataFrame, dates: list[str], grid_size_m: int, params: dict):
    selected = daily[daily.date.isin(set(dates))]
    lookup = {}
    pairs = set()
    for row in selected.itertuples(index=False):
        pair = (str(row.area_a), str(row.area_b))
        daymap = lookup.setdefault(pair, {}).setdefault(str(row.date), {})
        if row.window in daymap:
            raise ValueError(f"Duplicate daily OD key: {row.date}/{pair}/{row.window}")
        daymap[row.window] = (int(row.ab_count), int(row.ba_count))
        pairs.add(pair)

    candidates = set()
    for pair in sorted(pairs):
        for date in dates:
            windows = lookup.get(pair, {}).get(date, {})
            am = _direction(windows.get("AM", (0, 0)), params)[0]
            pm = _direction(windows.get("PM", (0, 0)), params)[0]
            if am and pm:
                candidates.add(pair)
                break

    cov_idx = coverage.set_index("local_date")
    events = []
    items_by_pair = {}
    for pair in sorted(candidates):
        tx = {}
        for date in dates:
            windows = lookup.get(pair, {}).get(date, {})
            am_counts = windows.get("AM", (0, 0))
            pm_counts = windows.get("PM", (0, 0))
            am_dir, am_dom, am_status = _direction(am_counts, params)
            pm_dir, pm_dom, pm_status = _direction(pm_counts, params)
            am_arrow = _actual_direction(pair, am_dir)
            pm_arrow = _actual_direction(pair, pm_dir)
            reversal = bool(am_arrow and pm_arrow and am_arrow != pm_arrow)
            tx[date] = {
                "am": f"AM:{am_arrow}" if am_arrow else None,
                "pm": f"PM:{pm_arrow}" if pm_arrow else None,
            }
            coverage_row = cov_idx.loc[date]
            events.append({
                "date": date,
                "spatial_scale": f"{grid_size_m}m",
                "area_a": pair[0],
                "area_b": pair[1],
                "corridor": f"{pair[0]}<->{pair[1]}",
                "am_ab_count": am_counts[0],
                "am_ba_count": am_counts[1],
                "am_total_rides": sum(am_counts),
                "pm_ab_count": pm_counts[0],
                "pm_ba_count": pm_counts[1],
                "pm_total_rides": sum(pm_counts),
                "am_direction": am_arrow,
                "pm_direction": pm_arrow,
                "am_dominance": am_dom,
                "pm_dominance": pm_dom,
                "am_status": am_status,
                "pm_status": pm_status,
                "reversal_status": "REVERSAL" if reversal else "NO_REVERSAL" if am_arrow and pm_arrow else "UNDETERMINED",
                "split": coverage_row.split,
                "day_type": coverage_row.day_type,
                "import_complete": bool(coverage_row.import_complete),
                "source_AM_coverage_status": coverage_row.AM_status,
                "source_PM_coverage_status": coverage_row.PM_status,
                "coverage_status": "unknown",
                "denominator_type": "observed_days",
            })
        items_by_pair[pair] = tx

    event_columns = [
        "date", "spatial_scale", "area_a", "area_b", "corridor", "am_ab_count", "am_ba_count",
        "am_total_rides", "pm_ab_count", "pm_ba_count", "pm_total_rides", "am_direction", "pm_direction",
        "am_dominance", "pm_dominance", "am_status", "pm_status", "reversal_status", "split", "day_type",
        "import_complete", "source_AM_coverage_status", "source_PM_coverage_status", "coverage_status", "denominator_type",
    ]
    event_frame = pd.DataFrame(events, columns=event_columns)
    rule_rows = []
    denominator = len(dates)
    for pair in sorted(candidates):
        tx = items_by_pair[pair]
        am_items = sorted({day["am"] for day in tx.values() if day["am"]})
        pm_items = sorted({day["pm"] for day in tx.values() if day["pm"]})
        for antecedent in am_items:
            am_path = antecedent.split(":", 1)[1]
            for consequent in pm_items:
                pm_path = consequent.split(":", 1)[1]
                if am_path != "->".join(reversed(pm_path.split("->"))):
                    continue
                antecedent_dates = [d for d in dates if tx[d]["am"] == antecedent]
                consequent_dates = [d for d in dates if tx[d]["pm"] == consequent]
                support_dates = [d for d in dates if tx[d]["am"] == antecedent and tx[d]["pm"] == consequent]
                joint_count = len(support_dates)
                antecedent_count = len(antecedent_dates)
                consequent_count = len(consequent_dates)
                support = joint_count / denominator if denominator else None
                confidence = joint_count / antecedent_count if antecedent_count else None
                lift = confidence / (consequent_count / denominator) if confidence is not None and consequent_count and denominator else None
                am_sufficient = sum(sum(lookup[pair].get(d, {}).get("AM", (0, 0))) >= params["n_min"] for d in dates)
                pm_sufficient = sum(sum(lookup[pair].get(d, {}).get("PM", (0, 0))) >= params["n_min"] for d in dates)
                rule_rows.append({
                    "corridor": f"{pair[0]}<->{pair[1]}",
                    "area_a": pair[0],
                    "area_b": pair[1],
                    "spatial_scale": f"{grid_size_m}m",
                    "antecedent": antecedent,
                    "consequent": consequent,
                    "direction_500m_AM" if grid_size_m == 500 else "direction_AM": am_path,
                    "joint_count": joint_count,
                    "antecedent_count": antecedent_count,
                    "consequent_count": consequent_count,
                    "observed_day_denominator": denominator,
                    "support_days": joint_count,
                    "support": support,
                    "confidence": confidence,
                    "lift": lift,
                    "am_sufficient_days": am_sufficient,
                    "pm_sufficient_days": pm_sufficient,
                    "support_dates": json.dumps(support_dates),
                    "antecedent_dates": json.dumps(antecedent_dates),
                    "consequent_dates": json.dumps(consequent_dates),
                    "denominator_type": "observed_days",
                    "coverage_status": "unknown",
                    "passes_min_support": bool(support is not None and support >= params["min_support"]),
                })
    direction_column = "direction_500m_AM" if grid_size_m == 500 else "direction_AM"
    rule_columns = [
        "corridor", "area_a", "area_b", "spatial_scale", "antecedent", "consequent", direction_column,
        "joint_count", "antecedent_count", "consequent_count", "observed_day_denominator", "support_days",
        "support", "confidence", "lift", "am_sufficient_days", "pm_sufficient_days", "support_dates",
        "antecedent_dates", "consequent_dates", "denominator_type", "coverage_status", "passes_min_support",
    ]
    rules = pd.DataFrame(rule_rows, columns=rule_columns)
    topk = rank_rules(rules, params["top_k"])
    for frame in (rules, topk):
        if not frame.empty:
            frame["spatial_scale"] = f"{grid_size_m}m"
    return {
        "dates": dates,
        "events": event_frame,
        "rules": rules,
        "topk": topk,
        "candidate_corridors": candidates,
        "observed_corridors": pairs,
    }


def rank_rules(rules: pd.DataFrame, k: int) -> pd.DataFrame:
    if rules.empty:
        empty = rules.copy()
        if "rank" not in empty.columns:
            empty["rank"] = pd.Series(dtype="int64")
        return empty
    eligible = rules[rules.passes_min_support].copy()
    if eligible.empty:
        eligible["rank"] = pd.Series(dtype="int64")
        return eligible
    eligible["joint_count"] = eligible.joint_count.astype(int)
    eligible = eligible.sort_values(
        ["support", "joint_count", "area_a", "area_b", "antecedent", "consequent"],
        ascending=[False, False, True, True, True, True],
        kind="mergesort",
    )
    return (
        eligible.drop_duplicates("corridor", keep="first")
        .head(k)
        .reset_index(drop=True)
        .assign(rank=lambda frame: range(1, len(frame) + 1))
    )


def region_parent_map(regions: pd.DataFrame) -> dict[str, str]:
    one_k = regions[regions.grid_size_m == 1000]
    five_h = regions[regions.grid_size_m == 500]
    valid_parents = set(one_k.region_id.astype(str))
    mapping = dict(zip(five_h.region_id.astype(str), five_h.parent_region.astype(str)))
    if five_h.parent_region.isna().any() or set(mapping.values()) - valid_parents:
        raise ValueError("Invalid 500m to 1000m parent mapping")
    return mapping


def _mapped_parent_column(table: pa.Table, column: str, mapping: dict[str, str]):
    ids = pa.array(list(mapping.keys()), type=pa.string())
    parents = pa.array(list(mapping.values()), type=pa.string())
    indices = pc.index_in(table[column], value_set=ids)
    if pc.any(pc.equal(indices, -1)).as_py():
        raise ValueError(f"Unmapped region in {column}")
    return pc.take(parents, indices)


def aggregate_to_parents(table: pa.Table, mapping: dict[str, str]) -> pd.DataFrame:
    parent_origin = _mapped_parent_column(table, "origin_region", mapping)
    parent_destination = _mapped_parent_column(table, "destination_region", mapping)
    table = table.append_column("parent_origin", parent_origin)
    table = table.append_column("parent_destination", parent_destination)
    table = table.filter(pc.not_equal(table["parent_origin"], table["parent_destination"]))
    grouped = table.group_by(["local_date", "window", "parent_origin", "parent_destination"]).aggregate(
        [("ride_count", "sum")]
    )
    return grouped.to_pandas().rename(columns={"ride_count_sum": "ride_count"})


def reconcile_tables(table_500: pa.Table, table_1000: pa.Table, parent_map: dict[str, str], data_dir: str | Path) -> pd.DataFrame:
    mapped = aggregate_to_parents(table_500, parent_map)
    direct = aggregate_to_parents(table_1000, {key: key for key in set(table_1000["origin_region"].to_pylist()) | set(table_1000["destination_region"].to_pylist())})
    columns = ["local_date", "window", "parent_origin", "parent_destination"]
    left = mapped.rename(columns={"ride_count": "mapped_500m_ride_count"})
    right = direct.rename(columns={"ride_count": "direct_1000m_ride_count"})
    joined = left.merge(right, how="outer", on=columns)
    for name in ("mapped_500m_ride_count", "direct_1000m_ride_count"):
        joined[name] = joined[name].fillna(0).astype("int64")
    joined["difference_500m_minus_1000m"] = joined.mapped_500m_ride_count - joined.direct_1000m_ride_count
    joined["is_match"] = joined.difference_500m_minus_1000m.eq(0)
    joined["input_data_dir"] = str(Path(data_dir).resolve())
    return joined.sort_values(columns).reset_index(drop=True)


def mapped_direction(direction: str, parent_map: dict[str, str]) -> str:
    origin, destination = direction.split("->", 1)
    return f"{parent_map[origin]}->{parent_map[destination]}"


def parse_corridor(value: str) -> tuple[str, str]:
    left, right = value.split("<->", 1)
    return left, right


