"""Run the real-data H2 experiments under the approved observed-day denominator."""

from __future__ import annotations

import itertools
import json
import math
import os
import platform
import sys
import time
import traceback
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import mlxtend
import numpy as np
import pandas as pd
from mlxtend.frequent_patterns import apriori, fpgrowth

from apriori_mining import encode_transactions
from real_data_adapter import load_real_data
from reversal_detection import get_main_direction


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
N_MIN, P_MIN, MIN_SUPPORT, MAX_LEN, TOP_K = 10, 2 / 3, 0.10, 2, 10
SENSITIVITY = [("Main", 10, 2 / 3), ("S1_n5", 5, 2 / 3), ("S2_n20", 20, 2 / 3),
               ("S3_p060", 10, 0.60), ("S4_p075", 10, 0.75)]


def _log(message: str) -> None:
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with (RESULTS / "h2_run.log").open("a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {message}\n")
    print(message, flush=True)


def _cohorts(coverage: pd.DataFrame):
    base = coverage[(coverage.import_complete) & (coverage.day_type.astype(str).str.lower() == "weekday")]
    year = base.local_date.astype(str).tolist()
    dev = base[base.split.astype(str).str.lower() == "development"].local_date.astype(str).tolist()
    hold = base[base.split.astype(str).str.lower() == "holdout"].local_date.astype(str).tolist()
    if not set(dev).issubset(year) or set(dev) & set(hold):
        raise ValueError("Development/Holdout date cohorts are not disjoint subsets of observed weekdays")
    return year, dev, hold


def _make_lookup(daily: pd.DataFrame, valid_dates: set[str]):
    selected = daily[daily.date.isin(valid_dates)]
    lookup = {}
    pairs = set()
    for row in selected.itertuples(index=False):
        pair = (str(row.area_a), str(row.area_b))
        day, window = str(row.date), str(row.window)
        daymap = lookup.setdefault(pair, {}).setdefault(day, {})
        if window in daymap:
            raise ValueError(f"Duplicate post-adapter daily key: {day}/{pair}/{window}")
        daymap[window] = (int(row.ab_count), int(row.ba_count))
        pairs.add(pair)
    return lookup, pairs


def _direction(counts, n_min, p_min):
    result = get_main_direction(counts[0], counts[1], n_min, p_min)
    return result.direction.value if result.direction else None, result.dominance, result.status.value


def _analyse_pool(lookup, pairs, dates, n_min, p_min):
    """Return qualifying corridors, per-corridor item transactions and daily records."""
    per_pair = {}
    event_rows = []
    qualified = set()
    for pair in sorted(pairs):
        for day, windows in lookup.get(pair, {}).items():
            # Missing source keys are zero observed rides; they cannot qualify.
            if "AM" not in windows or "PM" not in windows:
                continue
            am_dir, _, _ = _direction(windows["AM"], n_min, p_min)
            pm_dir, _, _ = _direction(windows["PM"], n_min, p_min)
            if am_dir and pm_dir:
                qualified.add(pair)
                break
    for pair in sorted(qualified):
        daily_items = {}
        for day in dates:
            windows = lookup.get(pair, {}).get(day, {})
            am_counts = windows.get("AM", (0, 0))
            pm_counts = windows.get("PM", (0, 0))
            am_dir, am_dom, am_status = _direction(am_counts, n_min, p_min)
            pm_dir, pm_dom, pm_status = _direction(pm_counts, n_min, p_min)
            items = []
            if am_dir:
                origin, dest = (pair if am_dir == "A_TO_B" else pair[::-1])
                items.append(f"AM:{origin}->{dest}")
            if pm_dir:
                origin, dest = (pair if pm_dir == "A_TO_B" else pair[::-1])
                items.append(f"PM:{origin}->{dest}")
            daily_items[day] = items
            reversal = bool(am_dir and pm_dir and am_dir != pm_dir)
            event_rows.append({
                "date": day, "spatial_scale": None, "area_a": pair[0], "area_b": pair[1],
                "am_ab_count": am_counts[0], "am_ba_count": am_counts[1],
                "pm_ab_count": pm_counts[0], "pm_ba_count": pm_counts[1],
                "am_direction": am_dir, "pm_direction": pm_dir,
                "am_dominance": am_dom, "pm_dominance": pm_dom,
                "am_status": am_status, "pm_status": pm_status,
                "reversal_status": "REVERSAL" if reversal else "NO_REVERSAL" if am_dir and pm_dir else "UNDETERMINED",
                "am_item": next((i for i in items if i.startswith("AM:")), None),
                "pm_item": next((i for i in items if i.startswith("PM:")), None),
                "coverage_status": "unknown", "denominator_type": "observed_days",
            })
        per_pair[pair] = daily_items
    return qualified, per_pair, event_rows


def _rules(qualified, per_pair, dates, split, min_support):
    N = len(dates)
    out = []
    for pair in sorted(qualified):
        tx = per_pair[pair]
        # For a canonical corridor, there is at most one AM direction and one PM direction per day.
        items = sorted({item for day in dates for item in tx[day]})
        am_items = [i for i in items if i.startswith("AM:")]
        pm_items = [i for i in items if i.startswith("PM:")]
        for ant in am_items:
            for cons in pm_items:
                a_path = ant.split(":", 1)[1]
                p_path = cons.split(":", 1)[1]
                if a_path != "->".join(reversed(p_path.split("->"))):
                    continue
                ant_dates = [d for d in dates if ant in tx[d]]
                cons_dates = [d for d in dates if cons in tx[d]]
                joint_dates = [d for d in dates if ant in tx[d] and cons in tx[d]]
                ac, cc, jc = len(ant_dates), len(cons_dates), len(joint_dates)
                support = jc / N if N else None
                confidence = jc / ac if ac else None
                lift = confidence / (cc / N) if ac and cc and N else None
                out.append({"corridor": f"{pair[0]}<->{pair[1]}", "area_a": pair[0], "area_b": pair[1],
                            "spatial_scale": None, "antecedent": ant, "consequent": cons,
                            "joint_count": jc, "antecedent_count": ac, "consequent_count": cc,
                            "observed_day_denominator": N,
                            "support": support, "confidence": confidence, "lift": lift,
                            "support_dates": json.dumps(joint_dates), "antecedent_dates": json.dumps(ant_dates),
                            "consequent_dates": json.dumps(cons_dates), "evaluation_split": split,
                            "denominator_type": "observed_days", "coverage_status": "unknown",
                            "passes_min_support": bool(support is not None and support >= min_support)})
    return pd.DataFrame(out)


def _topk(rules: pd.DataFrame, k=TOP_K):
    if rules.empty:
        return rules.copy()
    eligible = rules[rules.passes_min_support].copy()
    if eligible.empty:
        return eligible
    eligible["joint_count"] = eligible.joint_count.astype(int)
    # One undirected corridor per rank; its representative rule is the most observed direction.
    eligible = eligible.sort_values(["support", "joint_count", "area_a", "area_b", "antecedent", "consequent"],
                                    ascending=[False, False, True, True, True, True])
    return eligible.drop_duplicates("corridor", keep="first").head(k).reset_index(drop=True).assign(rank=lambda x: np.arange(1, len(x)+1))


def _sensitivity(lookup, pairs, dev_dates):
    results = []
    main_top = set()
    main_supports = {}
    main_directions = {}
    for name, n_min, p_min in SENSITIVITY:
        start = time.perf_counter()
        eligible, tx, _ = _analyse_pool(lookup, pairs, dev_dates, n_min, p_min)
        rules = _rules(eligible, tx, dev_dates, "Development", MIN_SUPPORT)
        top = _topk(rules)
        ids = set(top.corridor) if not top.empty else set()
        if name == "Main":
            main_top = ids
            if not rules.empty:
                main_supports = {(r.corridor,r.antecedent,r.consequent): r.support for r in rules.itertuples()}
                main_directions = {r.corridor: (r.antecedent,r.consequent) for r in _topk(rules).itertuples()}
        overlap = len(main_top & ids) if name != "Main" else len(ids)
        union = len(main_top | ids) if name != "Main" else len(ids)
        support_changes = []
        direction_changes = []
        if name != "Main" and not rules.empty:
            for r in rules.itertuples():
                key=(r.corridor,r.antecedent,r.consequent)
                if key in main_supports and r.support != main_supports[key]:
                    support_changes.append({"corridor":r.corridor,"rule":f"{r.antecedent} => {r.consequent}",
                                            "main_support":main_supports[key],"sensitivity_support":r.support,
                                            "delta":r.support-main_supports[key]})
            for r in _topk(rules).itertuples():
                if r.corridor in main_directions and (r.antecedent,r.consequent) != main_directions[r.corridor]:
                    direction_changes.append({"corridor":r.corridor,"main_rule":" => ".join(main_directions[r.corridor]),
                                              "sensitivity_rule":f"{r.antecedent} => {r.consequent}"})
        results.append({"experiment": name, "n_min": n_min, "p_min": p_min,
                        "observed_day_denominator": len(dev_dates), "eligible_corridor_count": len(eligible),
                        "all_reversal_rule_count": len(rules),
                        "reversal_rule_count": int(rules.passes_min_support.sum()) if not rules.empty else 0,
                        "top_k_corridors": json.dumps(top.corridor.tolist() if not top.empty else []),
                        "top_k_rules": json.dumps(top[["corridor","antecedent","consequent","joint_count","support"]].to_dict("records") if not top.empty else []),
                        "top_k_overlap_count_vs_main": overlap,
                        "top_k_jaccard_vs_main": overlap / union if union else None,
                        "support_changes_vs_main": json.dumps(support_changes),
                        "direction_changes_vs_main": json.dumps(direction_changes),
                        "all_rule_supports": json.dumps(rules[["corridor","antecedent","consequent","joint_count","support"]].to_dict("records") if not rules.empty else []),
                        "runtime_seconds": round(time.perf_counter() - start, 6),
                        "denominator_type": "observed_days", "coverage_status": "unknown"})
    return pd.DataFrame(results)


def _algorithm_comparison(eligible, per_pair, dev_dates):
    rows = []
    consistency = []
    run_summaries = []
    for pair in sorted(eligible):
        tx = per_pair[pair]
        metadata = {}
        for item in sorted({i for values in tx.values() for i in values}):
            window, path = item.split(":", 1)
            origin, destination = path.split("->")
            metadata[item] = {"window": window, "spatial_scale": "1000m", "area_a": pair[0], "area_b": pair[1],
                              "origin": origin, "destination": destination}
        data_prep_start = time.perf_counter()
        tx = {day: list(items) for day, items in tx.items()}
        prep_time = time.perf_counter() - data_prep_start
        encoded_start = time.perf_counter()
        encoded = encode_transactions(tx)
        encoding_time = time.perf_counter() - encoded_start
        alg_outputs = {}
        for algorithm, fn in (("Apriori", apriori), ("FP-Growth", fpgrowth)):
            mining_start = time.perf_counter()
            raw = fn(encoded, min_support=MIN_SUPPORT, use_colnames=True, max_len=MAX_LEN)
            mining_time = time.perf_counter() - mining_start
            norm = {tuple(sorted(frozenset(x))): int(round(float(s) * len(dev_dates)))
                    for x, s in zip(raw.itemsets, raw.support)}
            alg_outputs[algorithm] = norm
            filter_start = time.perf_counter()
            alg_reversals = set()
            for combo in norm:
                if len(combo) != 2:
                    continue
                ma, mb = (metadata.get(item,{}) for item in combo)
                if {ma.get("window"),mb.get("window")} == {"AM","PM"} and ma.get("origin")==mb.get("destination") and ma.get("destination")==mb.get("origin"):
                    alg_reversals.add(combo)
            filtering_time = time.perf_counter()-filter_start
            run_summaries.append({"algorithm":algorithm,"corridor":f"{pair[0]}<->{pair[1]}",
                                  "data_preparation_seconds":prep_time,"encoding_seconds":encoding_time,
                                  "mining_seconds":mining_time,"filtering_seconds":filtering_time,
                                  "total_seconds":prep_time+encoding_time+mining_time+filtering_time,
                                  "frequent_itemset_count":len(norm),"reversal_pattern_count":len(alg_reversals),
                                  "observed_day_denominator":len(dev_dates),"denominator_type":"observed_days"})
            for key, count in norm.items():
                rows.append({"algorithm": algorithm, "corridor": f"{pair[0]}<->{pair[1]}",
                             "itemset": json.dumps(key), "itemset_length": len(key), "support_count": count,
                             "support": count / len(dev_dates), "data_preparation_seconds": prep_time,
                             "encoding_seconds": encoding_time, "mining_seconds": mining_time,
                             "filtering_seconds": filtering_time, "total_seconds": prep_time + encoding_time + mining_time + filtering_time,
                             "observed_day_denominator": len(dev_dates), "denominator_type": "observed_days",
                             "coverage_status": "unknown"})
        # Independent counting enumerates every singleton and pair from the corridor's item vocabulary.
        vocabulary = sorted({i for values in tx.values() for i in values})
        direct = {}
        direct_start = time.perf_counter()
        for length in (1, 2):
            if length > MAX_LEN:
                continue
            for combo in itertools.combinations(vocabulary, length):
                count = sum(all(item in tx[day] for item in combo) for day in dev_dates)
                if count / len(dev_dates) >= MIN_SUPPORT:
                    direct[combo] = count
        direct_time = time.perf_counter() - direct_start
        filter_start = time.perf_counter()
        rev_keys = set()
        for combo in direct:
            if len(combo) != 2:
                continue
            a = metadata.get(combo[0], {}); b = metadata.get(combo[1], {})
            if {a.get("window"), b.get("window")} == {"AM", "PM"} and a.get("origin") == b.get("destination") and a.get("destination") == b.get("origin"):
                rev_keys.add(combo)
        direct_filtering_time=time.perf_counter()-filter_start
        run_summaries.append({"algorithm":"Direct Count","corridor":f"{pair[0]}<->{pair[1]}",
                              "data_preparation_seconds":prep_time,"encoding_seconds":0.0,
                              "mining_seconds":direct_time,"filtering_seconds":direct_filtering_time,
                              "total_seconds":prep_time+direct_time+direct_filtering_time,
                              "frequent_itemset_count":len(direct),"reversal_pattern_count":len(rev_keys),
                              "observed_day_denominator":len(dev_dates),"denominator_type":"observed_days"})
        for key, count in direct.items():
            rows.append({"algorithm":"Direct Count","corridor":f"{pair[0]}<->{pair[1]}",
                         "itemset":json.dumps(key),"itemset_length":len(key),"support_count":count,
                         "support":count/len(dev_dates),"data_preparation_seconds":prep_time,
                         "encoding_seconds":0.0,"mining_seconds":direct_time,"filtering_seconds":direct_filtering_time,
                         "total_seconds":prep_time+direct_time+direct_filtering_time,"observed_day_denominator":len(dev_dates),
                         "denominator_type":"observed_days","coverage_status":"unknown"})
        outputs_equal = alg_outputs["Apriori"] == alg_outputs["FP-Growth"] == direct
        reversal_outputs = {name: {key: value for key, value in output.items() if key in rev_keys}
                            for name, output in alg_outputs.items()}
        reversal_outputs["Direct Count"] = {key: value for key, value in direct.items() if key in rev_keys}
        reversal_equal = reversal_outputs["Apriori"] == reversal_outputs["FP-Growth"] == reversal_outputs["Direct Count"]
        consistency.append({"corridor": f"{pair[0]}<->{pair[1]}", "apriori_fpgrowth_equal": alg_outputs["Apriori"] == alg_outputs["FP-Growth"],
                            "apriori_direct_equal": alg_outputs["Apriori"] == direct,
                            "fpgrowth_direct_equal": alg_outputs["FP-Growth"] == direct,
                            "full_frequent_itemset_count": len(direct), "frequent_reversal_pattern_count": len(rev_keys),
                            "reversal_patterns_equal": reversal_equal,
                            "result_consistent": outputs_equal and reversal_equal})
        # Mark matching reversal pairs explicitly for downstream filtering/audit.
        for row in rows:
            if row["corridor"] == f"{pair[0]}<->{pair[1]}":
                row["is_reversal_pattern"] = tuple(json.loads(row["itemset"])) in rev_keys
                row["result_consistent"] = outputs_equal
    return pd.DataFrame(rows), pd.DataFrame(consistency), pd.DataFrame(run_summaries)


def main():
    RESULTS.mkdir(exist_ok=True)
    log = RESULTS / "h2_run.log"
    log.write_text("", encoding="utf-8")
    start = time.perf_counter()
    manifest = {"status": "running", "data_mode": "observed_data_only", "denominator_type": "observed_days",
                "coverage_status": "unknown", "parameters": {"n_min": N_MIN, "p_min": P_MIN,
                "min_support": MIN_SUPPORT, "max_len": MAX_LEN, "top_k": TOP_K},
                "sensitivity": [{"name": n, "n_min": a, "p_min": b} for n, a, b in SENSITIVITY],
                "development_holdout_separated": True, "holdout_used_for_selection": False,
                "python": sys.version, "platform": platform.platform(), "pandas": pd.__version__,
                "mlxtend": mlxtend.__version__, "inputs": {}}
    try:
        for scale in (1000, 500):
            _log(f"Loading and validating real {scale}m inputs")
            data = load_real_data(ROOT, scale)
            manifest["inputs"][str(scale)] = {"od_path": data.source_path, "coverage_path": str(ROOT / "preprocessing/necessary data/date_coverage.csv"),
                                              "regions_path": str(ROOT / "preprocessing/necessary data/regions.parquet"),
                                              "filtered_weekday_am_pm_rows": len(data.daily_counts),
                                              "unique_corridors": int(data.daily_counts[["area_a", "area_b"]].drop_duplicates().shape[0])}
            year_dates, dev_dates, hold_dates = _cohorts(data.coverage)
            manifest.setdefault("cohorts", {})[str(scale)] = {"full_year_observed_weekdays": len(year_dates),
                         "development_observed_weekdays": len(dev_dates), "holdout_observed_weekdays": len(hold_dates),
                         "coverage_status_values": sorted(set(data.coverage.AM_status) | set(data.coverage.PM_status)),
                         "import_complete_dates": int(data.coverage.import_complete.sum())}
            _log(f"{scale}m dates: all={len(year_dates)} development={len(dev_dates)} holdout={len(hold_dates)}; data keys={len(data.daily_counts)}")
            if scale == 1000:
                year_lookup, year_pairs = _make_lookup(data.daily_counts, set(year_dates))
                year_pool, year_tx, year_events = _analyse_pool(year_lookup, year_pairs, year_dates, N_MIN, P_MIN)
                dev_lookup, dev_pairs = _make_lookup(data.daily_counts, set(dev_dates))
                dev_pool, dev_tx, dev_events = _analyse_pool(dev_lookup, dev_pairs, dev_dates, N_MIN, P_MIN)
                year_rules = _rules(year_pool, year_tx, year_dates, "FullYear", MIN_SUPPORT)
                dev_rules = _rules(dev_pool, dev_tx, dev_dates, "Development", MIN_SUPPORT)
                if not year_rules.empty:
                    year_rules["spatial_scale"] = "1000m"
                if not dev_rules.empty:
                    dev_rules["spatial_scale"] = "1000m"
                for frame in (year_events, dev_events):
                    for row in frame:
                        row["spatial_scale"] = "1000m"
                event_frame = pd.DataFrame(year_events)
                cov_idx = data.coverage.set_index("local_date")
                event_frame["split"] = event_frame.date.map(cov_idx.split.astype(str).to_dict())
                event_frame["day_type"] = event_frame.date.map(cov_idx.day_type.astype(str).to_dict())
                event_frame["import_complete"] = event_frame.date.map(cov_idx.import_complete.to_dict())
                event_frame["source_AM_coverage_status"] = event_frame.date.map(cov_idx.AM_status.to_dict())
                event_frame["source_PM_coverage_status"] = event_frame.date.map(cov_idx.PM_status.to_dict())
                # Store observed event rows for candidate corridors across full-year weekdays;
                # absent OD keys are explicit zero counts within the cohort when evaluated.
                event_frame.to_parquet(RESULTS / "direction_events_1000m.parquet", index=False)
                candidates = []
                for label, pool, dates in (("FullYear", year_pool, year_dates), ("Development", dev_pool, dev_dates)):
                    src = year_tx if label == "FullYear" else dev_tx
                    for a, b in sorted(pool):
                        qualifying_dates = [d for d in dates if all(_direction(src[(a,b)][d] and (0,0), N_MIN, P_MIN)[0] for _ in [])]  # initialized below from event rows
                        qualifying_dates = []
                        for d in dates:
                            index = year_lookup if label == "FullYear" else dev_lookup
                            am = index.get((a,b),{}).get(d,{}).get("AM",(0,0))
                            pm = index.get((a,b),{}).get(d,{}).get("PM",(0,0))
                            if _direction(am,N_MIN,P_MIN)[0] and _direction(pm,N_MIN,P_MIN)[0]: qualifying_dates.append(d)
                        candidates.append({"pool": label, "spatial_scale": "1000m", "area_a": a, "area_b": b,
                                           "corridor": f"{a}<->{b}", "qualification_date_count": len(qualifying_dates),
                                           "qualification_dates": json.dumps(qualifying_dates), "n_min": N_MIN, "p_min": P_MIN,
                                           "observed_day_denominator": len(dates), "denominator_type":"observed_days",
                                           "coverage_status":"unknown"})
                pd.DataFrame(candidates).to_csv(RESULTS / "candidate_corridors.csv", index=False)
                year_rules.to_csv(RESULTS / "tidal_rules.csv", index=False)
                dev_rules.to_csv(RESULTS / "development_tidal_rules.csv", index=False)
                year_top = _topk(year_rules)
                dev_top = _topk(dev_rules)
                year_top.to_csv(RESULTS / "all_year_tidal_topk.csv", index=False)
                dev_top.to_csv(RESULTS / "development_tidal_topk.csv", index=False)
                # Frozen Development Top-10 only, independently evaluated on Holdout.
                hold_lookup, _ = _make_lookup(data.daily_counts, set(hold_dates))
                hold_pool = set((str(r.area_a),str(r.area_b)) for r in dev_top.itertuples())
                hold_tx = {}
                for pair in hold_pool:
                    hold_tx[pair] = {}
                    for d in hold_dates:
                        am = _direction(hold_lookup.get(pair,{}).get(d,{}).get("AM",(0,0)),N_MIN,P_MIN)[0]
                        pm = _direction(hold_lookup.get(pair,{}).get(d,{}).get("PM",(0,0)),N_MIN,P_MIN)[0]
                        values=[]
                        if am:
                            o,dd=(pair if am=="A_TO_B" else pair[::-1]); values.append(f"AM:{o}->{dd}")
                        if pm:
                            o,dd=(pair if pm=="A_TO_B" else pair[::-1]); values.append(f"PM:{o}->{dd}")
                        hold_tx[pair][d]=values
                hold_rules=[]
                for top_row in dev_top.to_dict("records"):
                    pair=(top_row["area_a"],top_row["area_b"])
                    a,c=top_row["antecedent"],top_row["consequent"]
                    ant=sum(a in hold_tx[pair][d] for d in hold_dates); cons=sum(c in hold_tx[pair][d] for d in hold_dates)
                    joint=sum(a in hold_tx[pair][d] and c in hold_tx[pair][d] for d in hold_dates)
                    conf=joint/ant if ant else None; sup=joint/len(hold_dates) if hold_dates else None
                    lift=conf/(cons/len(hold_dates)) if ant and cons and hold_dates else None
                    hold_rules.append({"corridor":top_row["corridor"],"spatial_scale":"1000m","antecedent":a,"consequent":c,
                                       "observed_day_denominator":len(hold_dates),"antecedent_count":ant,"consequent_count":cons,
                                       "joint_count":joint,"support":sup,"confidence":conf,"lift":lift,
                                       "evaluation_split":"Holdout","denominator_type":"observed_days","coverage_status":"unknown"})
                pd.DataFrame(hold_rules).to_csv(RESULTS / "holdout_topk_evaluation.csv",index=False)
                # E2: evaluate frozen Development candidate pool, never Holdout.
                _log(f"Running three-algorithm Development comparison on {len(dev_pool)} eligible corridors")
                e2_start=time.perf_counter(); comparison, consistency, run_summary=_algorithm_comparison(dev_pool,dev_tx,dev_dates)
                comparison.to_csv(RESULTS / "algorithm_itemsets_all.csv",index=False)
                reversal_comparison=comparison[comparison.is_reversal_pattern].reset_index(drop=True)
                reversal_comparison.to_csv(RESULTS / "algorithm_comparison.csv",index=False)
                consistency.to_csv(RESULTS / "algorithm_consistency.csv",index=False)
                run_summary.to_csv(RESULTS / "algorithm_run_summary.csv",index=False)
                # E4b sensitivity runs only on Development.
                _log("Running five Development-only threshold sensitivity configurations")
                sens=_sensitivity(dev_lookup,dev_pairs,dev_dates)
                sens.to_csv(RESULTS / "threshold_sensitivity.csv",index=False)
                manifest["e1"]={"status":"completed","year_candidate_corridors":len(year_pool),"development_candidate_corridors":len(dev_pool),
                                "year_tidal_rules":len(year_rules),"development_tidal_rules":len(dev_rules),
                                "year_rules_passing_minsup":int(year_rules.passes_min_support.sum()) if not year_rules.empty else 0,
                                "development_rules_passing_minsup":int(dev_rules.passes_min_support.sum()) if not dev_rules.empty else 0,
                                "year_min_support_count":math.ceil(MIN_SUPPORT*len(year_dates)),
                                "development_min_support_count":math.ceil(MIN_SUPPORT*len(dev_dates)),
                                "all_year_topk_rows":len(year_top),"development_topk_rows":len(dev_top),"holdout_evaluated_rules":len(hold_rules)}
                manifest["e2"]={"status":"completed","eligible_corridors":len(dev_pool),"reversal_itemset_rows":len(reversal_comparison),
                                "all_frequent_itemset_rows":len(comparison),
                                "consistency_corridors":len(consistency),"all_consistent":bool(consistency.result_consistent.all()) if len(consistency) else True,
                                "reversal_patterns_consistent":bool(consistency.reversal_patterns_equal.all()) if len(consistency) else True,
                                "corridor_day_empty_transactions":sum(sum(not dev_tx[pair][d] for d in dev_dates) for pair in dev_pool),
                                "elapsed_seconds":time.perf_counter()-e2_start,"min_support":MIN_SUPPORT,
                                "minimum_support_count":math.ceil(MIN_SUPPORT*len(dev_dates)),"max_len":MAX_LEN}
                manifest["e4b"]={"status":"completed","experiments":[{"name":n,"n_min":a,"p_min":b} for n,a,b in SENSITIVITY],
                                 "result_rows":len(sens),"development_days":len(dev_dates)}
                (RESULTS / "pattern_method.md").write_text(_method_text(manifest),encoding="utf-8")
                (RESULTS / "algorithm_results.md").write_text(_algorithm_text(consistency,reversal_comparison,run_summary),encoding="utf-8")
            else:
                if not all(str(a) <= str(b) for a,b in zip(data.daily_counts.area_a, data.daily_counts.area_b)):
                    raise ValueError("500m canonical corridor orientation check failed")
                sample = data.daily_counts.head(5000).copy()
                sample["decision_direction"] = sample.apply(
                    lambda r: _direction((int(r.ab_count),int(r.ba_count)),N_MIN,P_MIN)[0],axis=1)
                valid = int(sample.decision_direction.notna().sum())
                if not valid:
                    raise ValueError("500m real-data smoke sample contained no valid direction decisions")
                sample["spatial_scale"] = "500m"
                sample["denominator_type"] = "observed_days"
                sample["coverage_status"] = "unknown"
                sample.to_csv(RESULTS / "500m_smoke_sample.csv",index=False)
                manifest["scale_500m_smoke"]={"status":"completed","weekday_observed_days":len(year_dates),
                    "unique_corridors":int(data.daily_counts[["area_a","area_b"]].drop_duplicates().shape[0]),
                    "sample_rows":len(sample),"valid_direction_decisions":valid,
                    "fixed_orientation_check":"passed","separate_scale":"500m; not combined with 1000m"}
        manifest["status"]="completed" if manifest.get("e2",{}).get("all_consistent",False) else "completed_with_algorithm_inconsistency"
        manifest["elapsed_seconds"]=time.perf_counter()-start
        (RESULTS / "h2_run_manifest.json").write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding="utf-8")
        (RESULTS / "H2_completion_report.md").write_text(_completion_text(manifest),encoding="utf-8")
        _log(f"H2 run ended: {manifest['status']}; elapsed={manifest['elapsed_seconds']:.1f}s")
    except Exception as exc:
        manifest["status"]="failed"; manifest["error"]=repr(exc); manifest["traceback"]=traceback.format_exc()
        (RESULTS / "h2_run_manifest.json").write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding="utf-8")
        _log(f"FAILED: {exc}\n{traceback.format_exc()}")
        raise


def _method_text(m):
    return ("# H2 Pattern Mining Method\n\n"
            "This analysis uses real NYC Citi Bike OD aggregates and the approved observed-day denominator. `import_complete=True` defines an imported observation date; source AM/PM coverage labels remain `unknown`. An absent corridor/window OD key contributes zero *observed* rides, not a claim of zero real-world rides. Empty daily transactions remain in the date denominator. All reported metrics are `observed_data_only`.\n\n"
            "The primary cohort is weekday AM/PM at 1000m. A/B are fixed by lexical region ID. The candidate rule requires both windows to have at least n_min observed rides and a valid dominant direction on at least one cohort date. A rule is AM direction => opposite PM direction on the same corridor. Support is joint observed dates / observed dates; Confidence is joint / antecedent dates; Lift is Confidence / consequent support. Rules are retained with counts and support dates. Top-k requires support >= 0.10 and keeps at most one direction per undirected corridor, equivalent to at least 27 joint dates in the full-year cohort and 20 in Development. Full-year Top-10 is descriptive; Development Top-10 is selected without Holdout.\n\n"
            "The previous protocol excluded unknown coverage; it is superseded for denominator construction and candidate eligibility by the explicitly approved observed-day rule. No unknown values are relabeled. Development and Holdout are kept separate.\n")


def _algorithm_text(consistency, comparison, run_summary=None):
    ok=bool(consistency.result_consistent.all()) if len(consistency) else True
    counts={a:int((comparison.algorithm==a).sum()) for a in comparison.algorithm.unique()} if len(comparison) else {}
    timing=[]
    timing_data=run_summary if run_summary is not None else comparison.drop_duplicates(["algorithm","corridor"])
    if len(timing_data):
        per_corridor=timing_data.drop_duplicates(["algorithm","corridor"])
        for algorithm,frame in per_corridor.groupby("algorithm"):
            timing.append(f"| {algorithm} | {frame.mining_seconds.median():.6f} | {frame.encoding_seconds.median():.6f} | {frame.filtering_seconds.median():.6f} | {frame.total_seconds.median():.6f} |")
    timings="\n".join(["| Method | Median mining s | Median encoding s | Median filtering s | Median measured total s |","|---|---:|---:|---:|---:|"]+timing) if timing else "No timing rows."
    return ("# H2 Algorithm Comparison\n\n"
            "Apriori and FP-Growth use the same Development date-keyed transactions for each Development-eligible corridor, min_support=0.10 and max_len=2. Direct Count enumerates singleton and pair counts independently from those transactions. Empty Development dates are retained. `algorithm_comparison.csv` contains only frequent same-corridor AM-to-PM reverse pairs; `algorithm_itemsets_all.csv` preserves the wider within-corridor itemset audit.\n\n"
            f"Consistency across candidate corridors: **{'PASS' if ok else 'FAIL'}**. Checked corridor rows: {len(consistency)}. Primary reversal-pattern rows: {counts}. See `algorithm_consistency.csv`, `algorithm_itemsets_all.csv` and `algorithm_run_summary.csv` for full itemset agreement, counts and timings.\n\n"
            "Median per-corridor timings (seconds):\n\n"+timings+"\n\n"
            "Parquet loading, aggregation, and cohort selection are shared data preparation outside per-algorithm timing; total pipeline runtime is recorded in `h2_run_manifest.json` and `h2_run.log`.\n")


def _completion_text(m):
    c=m.get("cohorts",{}).get("1000",{})
    e=m.get("e1",{}); s=m.get("scale_500m_smoke",{}); e2=m.get("e2",{}); e4=m.get("e4b",{})
    top=pd.read_csv(RESULTS/"development_tidal_topk.csv") if (RESULTS/"development_tidal_topk.csv").exists() else pd.DataFrame()
    def table(frame):
        if frame.empty:
            return "No rules met the Top-10 support criterion."
        cols=["rank","corridor","antecedent","consequent","joint_count","support"]
        lines=["| Rank | Corridor | AM antecedent | PM consequent | Joint days | Support |","|---:|---|---|---|---:|---:|"]
        for r in frame[cols].itertuples(index=False,name=None):
            lines.append("| "+" | ".join(str(x) for x in r)+" |")
        return "\n".join(lines)
    toptext=table(top)
    alltop=pd.read_csv(RESULTS/"all_year_tidal_topk.csv") if (RESULTS/"all_year_tidal_topk.csv").exists() else pd.DataFrame()
    sensitivity=pd.read_csv(RESULTS/"threshold_sensitivity.csv") if (RESULTS/"threshold_sensitivity.csv").exists() else pd.DataFrame()
    sens_lines=["| Experiment | n_min | p_min | Dev observed days | Eligible corridors | Rules (support ≥0.10) | Top-10 overlap | Jaccard | Runtime s |","|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in sensitivity.itertuples():
        sens_lines.append(f"| {r.experiment} | {r.n_min} | {r.p_min:.4g} | {r.observed_day_denominator} | {r.eligible_corridor_count} | {r.reversal_rule_count} | {r.top_k_overlap_count_vs_main} | {r.top_k_jaccard_vs_main:.3f} | {r.runtime_seconds:.3f} |")
    sens_table="\n".join(sens_lines)
    return ("# H2 Completion Report\n\n"
            f"Run status: **{m['status']}**. Inputs are real source files; values are observed-data results, not population-wide ride claims.\n\n"
            "## E1\n\n"
            f"1000m weekdays: {c.get('full_year_observed_weekdays')} full-year observed days, {c.get('development_observed_weekdays')} Development days, {c.get('holdout_observed_weekdays')} Holdout days. Full-year eligible corridors/rules: {e.get('year_candidate_corridors')}/{e.get('year_tidal_rules')}; Development eligible corridors/rules: {e.get('development_candidate_corridors')}/{e.get('development_tidal_rules')}. Holdout evaluates the frozen Development Top-10 only.\n\n"
            "### Development Top-10\n\n"+toptext+"\n\n"
            "### Full-year descriptive Top-10\n\n"+table(alltop)+"\n\n"
            "## E2\n\n"
            f"Status: {e2.get('status')}; same Development dates and parameters across methods; consistency: {e2.get('all_consistent')}; compared candidate corridors: {e2.get('eligible_corridors')}.\n\n"
            "## E4b\n\n"
            f"Status: {e4.get('status')}; configurations: {len(e4.get('experiments',[]))}.\n\n"+sens_table+"\n\n"
            "## 500m smoke test\n\n"
            f"Status: {s.get('status')}; observed weekdays: {s.get('weekday_observed_days')}; corridors: {s.get('unique_corridors')}; scale remains isolated from 1000m.\n\n"
            "All metrics carry `denominator_type=observed_days` and `coverage_status=unknown`. See `h2_run_manifest.json` and `h2_run.log` for environment and input provenance.\n")


if __name__ == "__main__":
    main()
