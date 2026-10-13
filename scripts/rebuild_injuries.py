#!/usr/bin/env python3
"""Rebuild context.json's per-game injury lists from the collector's full store.

WHY THIS EXISTS (2026-10-02)
----------------------------
ESPN started answering the collector again on 2026-10-01 after blocking GitHub's
runner IPs since 09-09, so `data/injuries.json` is now a real 800+ entry store
and `context.json` carries injuries for the first time this season. But what
reaches the board is mostly noise, because `notable()` in the collector's
`context.py` has two faults that only show once the feed actually works:

  1. It drops status "Cleared" but NOT "Active". ESPN lists healthy players, so
     101 of the 238 chips on today's board say a quarterback is fine. A chip
     announcing that someone is healthy is not an injury report.

  2. Quarterbacks pass at ANY status (`tier == 1 or ...`), which was meant to
     mean "a QB's designation always matters" and in practice means "every
     backup QB on the roster takes a slot". The Bears showed three.

Those two combine into the real damage: `notable()` caps each side at six and
sorts tier first, so healthy quarterbacks EVICT real injuries before the list
ever reaches the page. Re-filtering `context.json` cannot recover them -- they
were dropped at collection time.

So this rebuilds each game's lists from `data/injuries.json`, which holds every
player and is committed on every run. The fix belongs in `context.py`, but this
sandbox cannot push to the repo (`access denied by the git proxy`, verified
2026-10-02), so the daily run applies it on the way to the board instead. The
same change has been handed to Austin for `context.py`; when he pushes it this
script becomes a no-op and can be dropped from the run.

WHAT IT DOES NOT TOUCH
----------------------
Weather, kickoff and matchup come through untouched. Only `injuries` is
rewritten, and only for games present in the slate.

Usage:
    python3 rebuild_injuries.py --store repo/data/injuries.json \
        --games games_fixed.json --context context.json --out context.json
"""
import argparse
import json
import sys

# Same intent as context.py's POSITION_TIER, with the gaps filled. Every
# position the store has actually emitted is listed; an unknown one is a hard
# error rather than a silent tier-5 drop, because a silent drop is this
# codebase's signature failure and a new position name is exactly how it would
# arrive.
POSITION_TIER = {
    "Quarterback": 1,
    "Left Tackle": 2, "Offensive Tackle": 2, "Center": 2, "Guard": 2,
    "Offensive Lineman": 2,
    "Wide Receiver": 3, "Running Back": 3, "Tight End": 3,
    "Cornerback": 3, "Defensive End": 3, "Edge": 3, "Linebacker": 3,
    "Safety": 4, "Defensive Tackle": 4, "Fullback": 4,
    "Place Kicker": 5, "Kicker": 5, "Punter": 5, "Long Snapper": 5,
}

# Short forms for the chip. The page renders `position` raw -- it has no
# abbrev() -- and "Quarterback" in a 10px chip crowds out the name.
POSITION_ABBR = {
    "Quarterback": "QB", "Left Tackle": "LT", "Offensive Tackle": "OT",
    "Center": "C", "Guard": "G", "Offensive Lineman": "OL",
    "Wide Receiver": "WR", "Running Back": "RB", "Tight End": "TE",
    "Cornerback": "CB", "Defensive End": "DE", "Edge": "EDGE",
    "Linebacker": "LB", "Safety": "S", "Defensive Tackle": "DT",
    "Fullback": "FB", "Place Kicker": "K", "Kicker": "K",
    "Punter": "P", "Long Snapper": "LS",
}

# How bad, for ordering. "Active" and "Cleared" are not designations and never
# reach the board; everything else is ranked worst first.
#
# ADDED 2026-10-13, deliberately, after the store emitted two statuses this
# table did not have (one player each, and the script halted rather than
# dropping them silently, which is the behaviour the runbook asks for):
#   "Reserve-Ret" -- the reserve/retired list (Lane Johnson, Philadelphia OT,
#                    detail "Personal"). Unavailable for the season, so it
#                    sorts with Injured Reserve.
#   "Suspension"  -- unavailable for the game and not an injury (Dre Greenlaw,
#                    San Francisco LB), so it sorts with Out.
# The out_or_ir diagnostic printed at the end is deliberately NOT widened to
# include either: it exists to prove the filter is being applied, and a stable
# definition is what makes its count comparable from run to run.
SEVERITY = {"Injured Reserve": 0, "Reserve-Ret": 0, "Out": 1, "Suspension": 1,
            "Doubtful": 2, "Questionable": 3}
NOT_A_DESIGNATION = {"Active", "Cleared"}

PER_SIDE = 6


def notable(entries):
    """A team's list, filtered to what can actually move a score.

    Differs from context.py's version in exactly two ways, both deliberate:
      - "Active" is excluded alongside "Cleared" (it is not a designation);
      - a quarterback is kept at any real designation, Questionable included,
        but a HEALTHY quarterback is not kept at all.
    Everyone down to tier 3 is kept at any designation; tier 4-5 only when
    they are Out or on IR, since a Questionable safety is not news.
    """
    out = []
    for e in entries or []:
        status = e.get("status", "")
        if status in NOT_A_DESIGNATION:
            continue
        if status not in SEVERITY:
            raise SystemExit(
                f"unknown injury status {status!r} for {e.get('name')!r} - add it to "
                "SEVERITY deliberately rather than letting it fall through")
        position = e.get("position", "")
        if position not in POSITION_TIER:
            raise SystemExit(
                f"unknown position {position!r} for {e.get('name')!r} - add it to "
                "POSITION_TIER deliberately rather than letting it default to tier 5")
        tier = POSITION_TIER[position]
        if tier >= 4 and status not in ("Out", "Injured Reserve"):
            continue
        out.append({
            "name": e["name"],
            "position": POSITION_ABBR[position],
            "status": status,
            "detail": e.get("detail", ""),
            "tier": tier,
            "changed_on": e.get("status_changed_on"),
        })
    # Tier first so the page's existing reading order is preserved, then
    # severity, then name for a stable list run to run.
    out.sort(key=lambda x: (x["tier"], SEVERITY[x["status"]], x["name"]))
    return out[:PER_SIDE]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True, help="repo data/injuries.json")
    ap.add_argument("--games", required=True, help="games_fixed.json")
    ap.add_argument("--context", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    store = json.load(open(args.store))
    games = json.load(open(args.games))
    ctx = json.load(open(args.context))

    by_team = {}
    for key, e in store.items():
        by_team.setdefault(e["team"], []).append(e)

    # A team the store has never heard of means the store's team names and the
    # odds feed's have drifted apart, which would silently blank that side.
    game_teams = set()
    for g in games.values():
        game_teams.add(g["home_team"])
        game_teams.add(g["away_team"])
    unknown = sorted(set(by_team) - game_teams)
    if unknown:
        raise SystemExit(f"store has teams the schedule does not: {unknown}")

    before = after = 0
    touched = 0
    for gid, entry in ctx.get("games", {}).items():
        g = games.get(gid)
        if not g:
            continue
        old = entry.get("injuries") or {"home": [], "away": []}
        before += len(old.get("home") or []) + len(old.get("away") or [])
        entry["injuries"] = {
            "home": notable(by_team.get(g["home_team"])),
            "away": notable(by_team.get(g["away_team"])),
        }
        after += len(entry["injuries"]["home"]) + len(entry["injuries"]["away"])
        touched += 1

    json.dump(ctx, open(args.out, "w"), indent=1, sort_keys=True)

    qbs = sum(1 for e in ctx["games"].values()
              for side in e["injuries"].values() for p in side if p["tier"] == 1)
    worst = sum(1 for e in ctx["games"].values()
                for side in e["injuries"].values() for p in side
                if p["status"] in ("Out", "Injured Reserve"))
    print(f"rebuilt injuries for {touched} games from {len(store)} store entries: "
          f"{before} chips -> {after}  (quarterbacks_on_report={qbs}  out_or_ir={worst})")
    if touched and after == 0:
        raise SystemExit("every game came back with no injuries - refusing to "
                         "write a board that claims the league is healthy")


if __name__ == "__main__":
    main()
