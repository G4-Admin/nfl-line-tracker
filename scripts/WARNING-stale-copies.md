# Three files in this folder are STALE and must not be used

Last checked 2026-10-13 against the live system.

| File | State |
|---|---|
| `pool_edge.py` | **2026-09-08 original.** Defines `SD`/`KEY_BUMPS` and `analyse`/`cover_probability`/`perfect_five`. The live engine defines `SIGMA = 13.5`/`KEY_MASS` and `price`/`best_side`/`calibrate_mu` — **no name in common**. `build_dashboard.py` ships `SIGMA` and `KEY_MASS` to the page as `DATA.model` inside a bare `try/except`, so building with THIS file importable publishes `model: null`: the page silently stops repricing and its own disagreement self-check stops running with it, while the build reports success. |
| `make_picks.py` | **2026-09-08 original. Has no post-deadline lock** — no `--prev-doc`, no `closing_watch`, no `write_fields`. Running it on a week that already carries submitted picks rebuilds the slate on that day's lines and writes over them. |
| `dashboard_template.html` | ~40KB, `renderBoard` era. The live template is ~189KB and carries the pool boxes, the Matchups view, week navigation, giving-vs-getting, team records and `applySnapshot`. Building from this one silently strips all of it. |

The correct copies live as project docs under `claude/scripts/`, and the template is
recovered from the published board's own `DATA` blob on every run — never from here.

Reconciling the engine pair (`pool_edge.py`, `make_picks.py`, plus `validate_sheet.py`,
which `make_picks.py` imports at module level) is open work. It is deliberately NOT a
copy-paste: the two engines are different models with different constants, so the three
files have to move together or `make_picks.py` breaks at import.

Nothing in `.github/workflows/collect.yml` imports any of these — the Action runs
`collect.py`, `context.py`, `results.py` and `build_sheet.py` only — so this is a
correctness hazard for anyone working in the repo, not a live collection failure.
