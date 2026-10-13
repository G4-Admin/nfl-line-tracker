#!/usr/bin/env python3
"""Re-bucket games into NFL weeks and verify the result.

Reconstructed locally on 2026-09-08 because scripts/fix_weeks.py has never been
committed to the collector repo (404 on raw). The rule and the three checks are
taken verbatim from claude/nfl-tracker-runbook.md:

  "take the game's US Eastern date and count seven-day blocks from the season's
   first game day"

  - no team appears twice in one week
  - every bye-free week holds exactly 16 games
  - bye-team-weeks total exactly 32, one per team

Committed to the repo 2026-10-13. Until then this file lived only as the project
doc claude/scripts/fix_weeks.py and every scheduled run reconstructed it by hand.
"""
import argparse
import collections
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def eastern_date(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET).date()


def rebucket(games):
    dates = {gid: eastern_date(g["commence_time"]) for gid, g in games.items()}
    anchor = min(dates.values())
    weeks = {gid: (d - anchor).days // 7 + 1 for gid, d in dates.items()}
    return weeks, anchor


def verify(games, weeks):
    problems = []
    by_week = collections.defaultdict(list)
    for gid, w in weeks.items():
        by_week[w].append(gid)

    teams = sorted({g["home_team"] for g in games.values()} |
                   {g["away_team"] for g in games.values()})
    n_teams = len(teams)

    # 1. no team appears twice in one week
    for w in sorted(by_week):
        seen = collections.Counter()
        for gid in by_week[w]:
            seen[games[gid]["home_team"]] += 1
            seen[games[gid]["away_team"]] += 1
        dupes = [t for t, c in seen.items() if c > 1]
        if dupes:
            problems.append(f"week {w}: team(s) playing twice: {', '.join(sorted(dupes))}")

    # 2. every bye-free week holds exactly n_teams/2 games
    full = n_teams // 2
    byes_by_week = {}
    for w in sorted(by_week):
        playing = set()
        for gid in by_week[w]:
            playing.add(games[gid]["home_team"])
            playing.add(games[gid]["away_team"])
        byes = [t for t in teams if t not in playing]
        byes_by_week[w] = byes
        if not byes and len(by_week[w]) != full:
            problems.append(f"week {w}: bye-free but holds {len(by_week[w])} games, expected {full}")

    # 3. bye-team-weeks total exactly n_teams, one per team
    bye_counts = collections.Counter(t for byes in byes_by_week.values() for t in byes)
    total_byes = sum(bye_counts.values())
    if total_byes != n_teams:
        problems.append(f"bye-team-weeks total {total_byes}, expected {n_teams}")
    multi = {t: c for t, c in bye_counts.items() if c != 1}
    if multi:
        problems.append("teams without exactly one bye: " +
                        ", ".join(f"{t}={c}" for t, c in sorted(multi.items())))
    for t in teams:
        if t not in bye_counts:
            problems.append(f"{t} has no bye week")

    return problems, by_week, byes_by_week


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", required=True)
    ap.add_argument("--out")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    with open(args.games) as f:
        games = json.load(f)

    before = collections.Counter(g.get("week") for g in games.values())
    weeks, anchor = rebucket(games)
    moved = sum(1 for gid, w in weeks.items() if games[gid].get("week") != w)
    for gid, w in weeks.items():
        games[gid]["week"] = w

    problems, by_week, byes = verify(games, weeks)

    print(f"anchor (season's first game day, US Eastern): {anchor} ({anchor.strftime('%A')})")
    print(f"regraded {moved} of {len(games)} games")
    print("before:", dict(sorted(before.items(), key=lambda kv: (kv[0] is None, kv[0]))))
    print("after :", {w: len(by_week[w]) for w in sorted(by_week)})
    print("byes  :", {w: len(byes[w]) for w in sorted(byes) if byes[w]})
    if problems:
        print("VERIFY FAILED:")
        for p in problems:
            print("  -", p)
    else:
        print("VERIFY OK: no team twice in a week; bye-free weeks full; one bye per team")

    if args.out:
        with open(args.out, "w") as f:
            json.dump(games, f, indent=1, sort_keys=True)
        print(f"wrote {args.out}")

    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
