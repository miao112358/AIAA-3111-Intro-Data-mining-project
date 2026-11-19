# Member C analysis

This directory contains C's H3 deliverables for the 2025 NYC Citi Bike tidal
corridor project. It consumes A's checked-in OD/coverage/region files and B's
frozen H2 corridor and rule outputs without changing either source.

## Outputs

| File | Purpose |
|---|---|
| `results/all_year_volume_topk.csv` | Popular Top-10 ranked by bidirectional weekday AM+PM observed rides in B's FullYear candidate pool |
| `results/ranking_comparison.csv` | Tidal-versus-popular ranking membership, ranks, metrics and overlap |
| `results/context_comparison.csv` | Fixed-corridor weekday/weekend and PM/MID context metrics |
| `results/monthly_profile.csv` | Twelve-month descriptive profile for B's full-year tidal Top-10 |
| `results/holdout_evaluation.csv` | Strict October–December evaluation of B's frozen Development Top-10 |
| `results/verification_notes.md` | Independent candidate, metric, algorithm and test audit of B's H2 handoff |
| `results/results.md` | Main findings and verification |
| `results/case_notes.md` | Reproducible examples for the report and presentation |
| `results/limitations.md` | Coverage, selection, spatial and interpretation limits |
| `results/C_handoff.md` | Short H3 handoff |
| `results/c_run_manifest.json` | Commit, parameters, environment, hashes and run summaries |

JSON summary files retain compact checks for each experiment. All CSV outputs
are flat, machine-readable tables with explicit numerators, denominators,
coverage status and observed-data labels.

## Reproduction

Use Python 3.10 or later with the packages in `requirements.txt`, from the
repository root:

```sh
python -m pip install -r C/requirements.txt
PYTHONPATH=C/src python C/src/run_member_c.py --root .
PYTHONPATH=C/src python -m unittest discover -s C/tests -v
```

The run does not mine Holdout or alter B's frozen candidate definitions. It
recomputes B's full-year and Holdout metrics independently as cross-checks.

## Coverage interpretation

The source window coverage status is `unknown`. Following the approved H2
protocol, the analysis uses imported observed dates and treats absent sparse OD
keys as zero observed rides. This is not a claim that every real-world ride was
captured. Outputs must retain the `observed_data_only` and `unknown` labels.

