# One file in this folder is STALE and must not be used

Last checked 2026-10-13.

| File | State |
|---|---|
| `dashboard_template.html` | **2026-09-08 original, ~40KB.** The live template is ~189KB and carries the pool boxes, the Matchups view, week navigation, giving-vs-getting, team records and `applySnapshot`. Building from this one silently strips all of it. |

The live template is **recovered from the published board's own `DATA` blob on every run**, never
from here — see `claude/nfl-tracker-runbook.md` §6. It is not committed because it is an output of
the board, not a source file, and a committed copy would go stale again within days.

`build_dashboard.py`'s `--template` default deliberately names a file that does not exist, so a run
that forgets to pass `--template` fails loudly instead of quietly building from the stale copy.
**Do not "fix" that default by pointing it here.**

## Resolved 2026-10-13 — the engine migration

`pool_edge.py`, `make_picks.py`, `validate_sheet.py` and `build_dashboard.py` were the
2026-09-08 originals until today and are now the live versions. They had to move as one commit:
the old `make_picks.py` imported `analyse, cover_probability, perfect_five, _margin_pmf, fmt_line`
from `pool_edge`, and **the current engine defines none of those** — it defines
`price`/`best_side`/`calibrate_mu` over `SIGMA`/`KEY_MASS`. Committing either file alone would
have left the other broken at import.

What the old copies would have done, measured rather than assumed:

- `make_picks.py` had **no `--prev-doc`, no `closing_watch`, no `write_fields`** — no post-deadline
  lock of any kind. Run on a week already carrying submitted picks it rebuilds the slate on that
  day's lines and writes over them.
- `pool_edge.py` defines no `SIGMA` and no `KEY_MASS`, so `build_dashboard.py` shipped
  `model: null` to the page: the in-browser reprice stops working and its own disagreement
  self-check stops with it, while the build reports success.
- `build_dashboard.py` still accepted `--picks` and `--scorecard` (both long removed) and had no
  `--baselines`, `--venues` or `--teams`.

Before the commit, the new set was tested against the stored Week 5 — a real locked week carrying
Austin's five — and confirmed to return the submitted slate untouched, preserve `state`, emit
`closing_watch` for all five legs, and restrict `write_fields` to `slate`, `market_date`,
`closing_watch` and `locked`.

**Nothing in `.github/workflows/collect.yml` imports any of these.** The Action runs `collect.py`,
`context.py`, `results.py` and `build_sheet.py`; `selftest.py` imports `collect` alone. So this
commit cannot affect collection.
