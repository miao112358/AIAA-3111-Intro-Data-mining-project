from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from e4c_common import E4C_DIR, analyze_scale, daily_corridors, load_parameters, load_scale, resolve_data_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Run full-year weekday 500m tidal pattern mining.")
    parser.add_argument("--data-dir", help="Preprocessing output directory; defaults to the latest complete run.")
    parser.add_argument("--parameters", help="JSON parameter file; defaults to E4c/parameters.json.")
    args = parser.parse_args()

    data_dir = resolve_data_dir(args.data_dir)
    params = load_parameters(args.parameters)
    coverage, dates, table, _, _ = load_scale(data_dir, 500)
    daily = daily_corridors(table, 500)
    result = analyze_scale(daily, coverage, dates, 500, params)

    events_path = E4C_DIR / "500m_direction_events.parquet"
    rules_path = E4C_DIR / "500m_tidal_rules.csv"
    topk_path = E4C_DIR / "500m_tidal_topk.csv"
    result["events"].to_parquet(events_path, index=False)
    result["rules"].to_csv(rules_path, index=False)
    result["topk"].to_csv(topk_path, index=False)
    print(json.dumps({
        "data_dir": str(data_dir),
        "imported_weekdays": len(dates),
        "observed_corridors": len(result["observed_corridors"]),
        "candidate_corridors": len(result["candidate_corridors"]),
        "reversal_rules": len(result["rules"]),
        "rules_passing_min_support": int(result["rules"].passes_min_support.sum()) if not result["rules"].empty else 0,
        "top_k_rows": len(result["topk"]),
        "outputs": [str(events_path), str(rules_path), str(topk_path)],
        "parameter_status": params.get("parameter_status", "unspecified"),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
