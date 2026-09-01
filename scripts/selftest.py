#!/usr/bin/env python3
"""Offline test of the collector logic against a scripted 5-day sequence.

No network. Stubs fetch_odds with fixtures that exercise:
  day 1  baseline capture of two games already on the board
  day 2  a 1.0 pt move toward the home team, crossing the key number 3
  day 3  a same-day re-run (must overwrite, not append, the day's point)
  day 4  a brand new game posting for the first time -> genuine opener
  day 5  a game that has kicked off -> frozen, no longer updated
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect  # noqa: E402

NOW = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)


def ev(gid, away, home, days_out, lines, base=NOW):
    """lines: {book: home_spread}"""
    return {
        "id": gid,
        "commence_time": (base + timedelta(days=days_out)).isoformat().replace("+00:00", "Z"),
        "home_team": home,
        "away_team": away,
        "bookmakers": [
            {"key": bk, "title": bk, "markets": [{"key": "spreads", "outcomes": [
                {"name": home, "price": -110, "point": pt},
                {"name": away, "price": -110, "point": -pt},
            ]}]}
            for bk, pt in lines.items()
        ],
    }


def run(fixture, now):
    collect.fetch_odds = lambda key: (fixture, {"remaining": "480", "used": "20", "cost": "1"})

    class FrozenDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    real = collect.datetime
    collect.datetime = FrozenDT
    try:
        collect.main()
    finally:
        collect.datetime = real


def read(name):
    with open(os.path.join(collect.DATA, name)) as f:
        return json.load(f)


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}" + ("" if ok else f" want {want!r}"))
    return ok


def main():
    tmp = tempfile.mkdtemp()
    collect.DATA = tmp
    collect.SNAPSHOTS = os.path.join(tmp, "snapshots.csv")
    collect.GAMES = os.path.join(tmp, "games.json")
    collect.MOVEMENT = os.path.join(tmp, "movement.json")
    os.environ["ODDS_API_KEY"] = "test"
    os.environ["SEASON_ANCHOR"] = "2026-09-08"

    failures = []
    A = ("g_kc_buf", "Kansas City Chiefs", "Buffalo Bills")
    B = ("g_sf_sea", "San Francisco 49ers", "Seattle Seahawks")
    C = ("g_dal_phi", "Dallas Cowboys", "Philadelphia Eagles")

    print("day 1  baseline")
    run([ev(*A, 12, {"draftkings": -2.5, "fanduel": -2.5, "betmgm": -3.0}),
         ev(*B, 13, {"draftkings": 1.5, "fanduel": 2.0, "betmgm": 1.5})], NOW)
    g = read("games.json")
    failures += [not check("games tracked", len(g), 2)]
    failures += [not check("A opener", g["g_kc_buf"]["opened"]["consensus"], -2.5)]
    failures += [not check("A opener flagged as baseline",
                           g["g_kc_buf"]["opened"]["source"], "baseline_at_setup")]
    # Sept 13 sits in the week that starts Tue Sept 8 -> Week 1
    failures += [not check("A week", g["g_kc_buf"]["week"], 1)]
    m = read("movement.json")
    failures += [not check("day1 movers are baselines",
                           {x["event"] for x in m["movers"]}, {"baseline"})]

    print("day 2  move through the key number")
    d2 = NOW + timedelta(days=1)
    run([ev(*A, 11, {"draftkings": -3.5, "fanduel": -3.5, "betmgm": -3.5}, d2),
         ev(*B, 12, {"draftkings": 1.5, "fanduel": 1.5, "betmgm": 1.5}, d2)], d2)
    g, m = read("games.json"), read("movement.json")
    failures += [not check("A current", g["g_kc_buf"]["current"]["consensus"], -3.5)]
    failures += [not check("A opener unchanged", g["g_kc_buf"]["opened"]["consensus"], -2.5)]
    failures += [not check("A history length", len(g["g_kc_buf"]["history"]), 2)]
    mv = [x for x in m["movers"] if x["game_id"] == "g_kc_buf"][0]
    failures += [not check("A delta", mv["delta"], -1.0)]
    failures += [not check("A crossed key number", mv["crossed"], 3)]
    failures += [not check("B not a mover (0.25 median shift)",
                           [x["game_id"] for x in m["movers"]], ["g_kc_buf"])]

    print("day 2  re-run same day (idempotent)")
    run([ev(*A, 11, {"draftkings": -4.0, "fanduel": -4.0, "betmgm": -4.0}, d2),
         ev(*B, 12, {"draftkings": 1.5, "fanduel": 1.5, "betmgm": 1.5}, d2)],
        d2 + timedelta(hours=6))
    g, m = read("games.json"), read("movement.json")
    failures += [not check("history still 2 points after re-run",
                           len(g["g_kc_buf"]["history"]), 2)]
    failures += [not check("re-run updated the value",
                           g["g_kc_buf"]["current"]["consensus"], -4.0)]
    mv = [x for x in m["movers"] if x["game_id"] == "g_kc_buf"][0]
    failures += [not check("re-run delta measured from prior day, not itself",
                           mv["delta"], -1.5)]

    print("day 3  a new game posts")
    d3 = NOW + timedelta(days=2)
    run([ev(*A, 10, {"draftkings": -4.0, "fanduel": -4.0, "betmgm": -4.0}, d3),
         ev(*B, 11, {"draftkings": 1.5, "fanduel": 1.5, "betmgm": 1.5}, d3),
         ev(*C, 20, {"draftkings": -6.5, "fanduel": -7.0, "betmgm": -6.5}, d3)], d3)
    g, m = read("games.json"), read("movement.json")
    failures += [not check("C captured", "g_dal_phi" in g, True)]
    failures += [not check("C is a true opener",
                           g["g_dal_phi"]["opened"]["source"], "first_capture")]
    failures += [not check("C opener value", g["g_dal_phi"]["opened"]["consensus"], -6.5)]
    failures += [not check("C flagged as new_line",
                           [x["event"] for x in m["movers"] if x["game_id"] == "g_dal_phi"],
                           ["new_line"])]

    print("day 4  game A has kicked off")
    d4 = NOW + timedelta(days=12)
    run([ev(*B, 1, {"draftkings": 3.0, "fanduel": 3.0, "betmgm": 3.0}, d4),
         ev(*C, 10, {"draftkings": -6.5, "fanduel": -7.0, "betmgm": -6.5}, d4)], d4)
    g = read("games.json")
    failures += [not check("A frozen at its closing line",
                           g["g_kc_buf"]["current"]["consensus"], -4.0)]
    failures += [not check("A history did not grow", len(g["g_kc_buf"]["history"]), 3)]
    failures += [not check("B moved 1.5 toward home",
                           g["g_sf_sea"]["current"]["consensus"], 3.0)]

    with open(collect.SNAPSHOTS) as f:
        rows = len(f.readlines()) - 1
    # 6 + 6 + 6 + 9 + 6 across five runs
    failures += [not check("snapshot rows appended (nothing overwritten)", rows, 33)]

    shutil.rmtree(tmp)
    bad = sum(failures)
    print(f"\n{'ALL PASS' if not bad else str(bad) + ' FAILURES'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
