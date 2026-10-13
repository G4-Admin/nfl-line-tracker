#!/usr/bin/env python3
"""Freeze a tracking baseline for every game that has gone live.

PATCHED 2026-09-14 -- "the game they most recently played" means each team's
IMMEDIATELY PRECEDING SCHEDULED fixture. Read the --as-of help below before
changing anything here; the pre-patch version baselined 220 games across weeks
3-18 in a single run, and baselines are frozen forever.

Committed to the repo 2026-10-13. Until then this file lived only as the project
doc claude/scripts/set_baselines.py.
"""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone

FINISH_AFTER = timedelta(hours=3.5)


def kickoff(g):
    return datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", required=True)
    ap.add_argument("--existing", default="baselines.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--now", default=None, help="override 'now' (ISO, UTC) for testing")
    ap.add_argument("--as-of", default=None,
                    help="clock used ONLY for 'has the prior fixture finished'.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    now = (datetime.fromisoformat(args.now).replace(tzinfo=timezone.utc)
           if args.now else datetime.now(timezone.utc))

    as_of = (datetime.fromisoformat(args.as_of).replace(tzinfo=timezone.utc)
             if args.as_of else now)

    with open(args.games) as f:
        games = json.load(f)

    baselines = {}
    if os.path.exists(args.existing):
        with open(args.existing) as f:
            baselines = json.load(f)
    before = len(baselines)

    by_team = {}
    for gid, g in games.items():
        for t in (g["home_team"], g["away_team"]):
            by_team.setdefault(t, []).append(gid)
    for t in by_team:
        by_team[t].sort(key=lambda gid: kickoff(games[gid]))

    def prior_scheduled_finished(team, before_gid):
        sched = by_team[team]
        try:
            i = sched.index(before_gid)
        except ValueError:
            return None
        if i == 0:
            return None                   # no prior fixture: week 1
        prev = sched[i - 1]
        if kickoff(games[prev]) + FINISH_AFTER <= as_of:
            return prev
        return None

    added = []
    for gid, g in sorted(games.items(), key=lambda kv: kickoff(kv[1])):
        if gid in baselines:
            continue
        if kickoff(g) <= as_of:
            continue
        cur = g.get("current") or {}
        if cur.get("consensus") is None:
            continue

        if g["week"] == 1:
            trigger, note = "week1_game_week_open", (
                "Week 1 has no prior game to anchor to; baselined when its game week opened.")
        else:
            prior = [prior_scheduled_finished(t, gid) for t in (g["home_team"], g["away_team"])]
            if not all(prior):
                continue
            trigger, note = "both_teams_last_game_final", (
                "Both teams had finished their most recent game, so this line already "
                "reflects those results.")

        baselines[gid] = {
            "consensus": cur["consensus"],
            "n_books": cur.get("n_books"),
            "snapshot_date": cur.get("date"),
            "set_at": now.isoformat(timespec="seconds"),
            "trigger": trigger,
            "note": note,
            "week": g["week"],
        }
        added.append((gid, g["week"], f'{g["away_team"]} @ {g["home_team"]}', cur["consensus"],
                      cur.get("n_books"), cur.get("date")))

    if not args.dry_run:
        with open(args.out, "w") as f:
            json.dump(baselines, f, indent=1, sort_keys=True)

    print(f"baselines: {before} existing -> {len(baselines)} ({len(added)} new)"
          + ("  [DRY RUN, nothing written]" if args.dry_run else ""))
    for gid, wk, matchup, c, nb, d in added:
        print(f"  + W{wk} {matchup}  {c:+.1f}  n_books={nb}  snap={d}")
    if not added:
        print("  (no game newly qualified)")
    if not args.dry_run:
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
