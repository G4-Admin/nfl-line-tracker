# NFL line tracker

Captures the point spread for every upcoming NFL regular season game once a day,
from the first line it sees through the day before kickoff, and keeps the whole
history in this repo.

The reasoning layer — *why* a line moved — runs separately in Claude and reads
the files this repo produces.

## Setup (about five minutes)

1. **Get an Odds API key.** Free tier at <https://the-odds-api.com> — 500 credits
   a month. This tracker spends 1 credit per run, so ~30 a month.

2. **Create a repo** on GitHub named `nfl-line-tracker` and push these files to it.
   Make it **public**: the daily analysis run reads the committed data over
   `raw.githubusercontent.com`, which needs a token for private repos. Nothing
   sensitive is committed — the API key lives in Actions secrets and never touches
   the repo, and the contents are published betting lines.

   ```bash
   git init -b main
   git add .
   git commit -m "NFL line tracker"
   git remote add origin git@github.com:<you>/nfl-line-tracker.git
   git push -u origin main
   ```

3. **Add the key as a secret.** Repo → Settings → Secrets and variables → Actions
   → New repository secret. Name it `ODDS_API_KEY`.

4. **Let Actions write to the repo.** Settings → Actions → General → Workflow
   permissions → *Read and write permissions*. The workflow commits each day's
   data back to `main`.

5. **Run it once by hand.** Actions tab → *Collect NFL lines* → Run workflow.
   It should commit a `data/` folder within a minute. That first run is the
   baseline.

6. **Tell Claude the repo name** so the analysis run knows where to read from.

## What runs

`.github/workflows/collect.yml` fires at 15:00 UTC daily (9:00 am Denver during
MDT, 8:00 am after the November clock change) and can also be run on demand.

| Step | Script | Output |
|---|---|---|
| Pull spreads | `scripts/collect.py` | `data/snapshots.csv`, `data/games.json`, `data/movement.json` |
| Rebuild the sheet | `scripts/build_sheet.py` | `data/lines_master.xlsx` |

`scripts/build_dashboard.py` renders the published dashboard from `games.json`
plus a notes file; Claude runs that one, not the workflow.

## The data

**`games.json`** — the state of the tracker. One entry per game:

```jsonc
{
  "game_id":   "…",
  "week":      1,
  "commence_time": "2026-09-13T17:00:00Z",
  "home_team": "Cincinnati Bengals",
  "away_team": "Tampa Bay Buccaneers",
  "opened":    { "date": "2026-09-01", "consensus": -3.5, "source": "baseline_at_setup" },
  "history":   [ { "date": "2026-09-01", "consensus": -3.5, "n_books": 8,
                   "min": -4.0, "max": -3.0, "draftkings": -3.5, … } ],
  "current":   { … }
}
```

**`snapshots.csv`** — every book's number at every run, never rewritten. This is
the raw record; `games.json` is the derived view.

**`movement.json`** — small digest of the latest run: which games moved, by how
much, and whether the move crossed a key number. This is what the Claude run
reads first.

### Conventions

- Spreads are quoted **from the home team's side**. `-3.5` means the home team
  lays 3.5; `+2.5` means the visitor is favored by 2.5.
- The tracked number is the **median across every US book** the API returns that
  day, rounded to the nearest half point. Individual books are kept too.
- A **negative move** means the line went toward the home team.
- `opened.source` is `baseline_at_setup` for games already on the board the day
  tracking started, and `first_capture` for games whose line posted afterward —
  only the second kind is a true opening line.
- Games stop updating once they kick off; their last snapshot stands as the close.

## Cost and limits

One run costs 1 API credit (`markets × regions` = `spreads × us`). The free tier's
500 credits a month leaves plenty of headroom — twice-daily collection would still
only spend ~60.

If a run fails, the workflow leaves the previous data untouched; a missed day is a
gap in the history, not corruption. Re-running the same day overwrites that day's
point rather than adding a second one.

## Tests

```bash
python scripts/selftest.py
```

Runs the collector against a scripted five-day sequence with no network: baseline
capture, a move through a key number, a same-day re-run, a new game posting, and a
game that has kicked off.
