#!/usr/bin/env python3
"""
WRG pool pick engine.

The pool freezes its lines Thursday morning; picks are due noon Arizona Saturday.
That two-day lag is the edge: you hold a Thursday price while the market keeps
moving. This script measures that gap, converts it to a cover probability, and
ranks the slate.

  python scripts/pool_edge.py --pool pool/week01.json [--games data/games.json]

Pool sheet format (transcribed from the Commish's email):

  {
    "week": 1,
    "sheet_date": "2026-09-10",
    "field_size": 55,
    "mnf": {"home": "Chicago Bears", "away": "Minnesota Vikings"},
    "lines": [
      {"home": "Buffalo Bills", "away": "Kansas City Chiefs", "home_spread": 2.5}
    ]
  }

`home_spread` is quoted from the home side, same convention as the collector:
-3.5 means the home team lays 3.5.

Everything this prints is an estimate built on a model of NFL margins. The points
of line value are hard numbers; the probabilities are not. Trust the ranking more
than the decimals.
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# NFL margin model
# ---------------------------------------------------------------------------
# Final margins cluster hard on the field-goal and touchdown numbers. A plain
# normal curve misses that, and missing it is exactly the error that matters,
# because most line value is won or lost crossing 3 and 7.
#
# Model: a normal over integer margins, multiplied by a bump table at the key
# numbers, then renormalized. This approximates a public distribution -- it is not
# a proprietary edge -- and exists only to turn points of line value into a
# probability. Note that a push counts as a LOSS in this pool, so a game sitting
# exactly on 3 with no line value is roughly a 46% pick, not a coinflip.

SD = 13.0
KEY_BUMPS = {3: 1.90, 7: 1.60, 6: 1.05, 10: 1.35, 4: 1.00, 14: 1.15, 1: 0.72, 2: 0.88}
# Fitted against published NFL margin frequencies, weighted by a realistic
# distribution of spreads. Model vs published, exact final margin:
#   3 -> 9.7% (9.5)   4 -> 5.0% (4.6)   6 -> 5.0% (5.0)
#   7 -> 7.4% (7.0)  10 -> 5.5% (5.5)  14 -> 3.7% (3.5)
# Implied sd 12.7 against a real ~13.3.
# KNOWN WEAKNESS: margins of 1 and 2 come out around 3.7% and 4.5% against real
# rates near 2.6% and 2.0% -- one- and two-point NFL finishes are rarer than any
# smooth curve wants them to be. That inflates push risk on lines sitting exactly
# on 1 or 2. It does not affect 3 and 7, which is where the money is.
MARGIN_RANGE = range(-70, 71)


def _raw_pmf(centre):
    raw = {}
    for m in MARGIN_RANGE:
        z = (m - centre) / SD
        raw[m] = math.exp(-0.5 * z * z) * KEY_BUMPS.get(abs(m), 1.0)
    total = sum(raw.values())
    return {m: v / total for m, v in raw.items()}


def _imbalance(centre, true_line):
    """P(home covers true_line) - P(home fails to), pushes excluded."""
    pmf = _raw_pmf(centre)
    hi = sum(p for m, p in pmf.items() if m + true_line > 1e-9)
    lo = sum(p for m, p in pmf.items() if m + true_line < -1e-9)
    return hi - lo


def _margin_pmf(true_line):
    """P(home margin = m) given the market's fair line.

    The market line is the price at which action balances, so it must come out as
    the MEDIAN of this distribution. Simply centring the curve on -true_line and
    then applying key-number bumps double-counts: the market has already priced
    the 3-spike when it chose 2.5 over 3. That left a spurious tilt of up to 1.5
    points on games with no line value at all -- enough to float a zero-edge game
    into a recommended slate.

    So: centre the curve, then solve for the small shift that makes the market
    line the true balance point. After this, a pick with no line value comes out
    at (1 - push) / 2, and the push penalty is the only asymmetry left -- which is
    exactly the real one, since a push loses in this pool.
    """
    lo, hi = -true_line - 4.0, -true_line + 4.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if _imbalance(mid, true_line) > 0:
            hi = mid
        else:
            lo = mid
    return _raw_pmf((lo + hi) / 2)


def cover_probability(pool_spread, market_spread, side):
    """Probability the given side covers `pool_spread`, if `market_spread` is fair.

    side is "home" or "away". Ties (pushes) count as losses in this pool, so they
    are excluded from the winning mass rather than split out.
    """
    pmf = _margin_pmf(market_spread)
    win = 0.0
    push = 0.0
    for m, p in pmf.items():
        # Home covers when margin + pool_spread > 0.
        edge = m + pool_spread
        if abs(edge) < 1e-9:
            push += p
        elif (edge > 0) == (side == "home"):
            win += p
    return win, push


# ---------------------------------------------------------------------------
# Matching pool lines to collected market data
# ---------------------------------------------------------------------------

def _key(name):
    return name.strip().lower().replace(".", "")


def match_game(entry, games):
    """Find the collector's game for a pool-sheet entry, by team names."""
    h, a = _key(entry["home"]), _key(entry["away"])
    for g in games.values():
        gh, ga = _key(g["home_team"]), _key(g["away_team"])
        if (gh == h and ga == a):
            return g
        # tolerate short names: "Bills" vs "Buffalo Bills"
        if gh.endswith(h) and ga.endswith(a):
            return g
        if h.endswith(gh.split()[-1]) and a.endswith(ga.split()[-1]):
            return g
    return None


def market_on(game, date_str):
    """Consensus as of a given date, or the closest earlier snapshot."""
    best = None
    for p in game["history"]:
        if p["date"] <= date_str:
            best = p
        else:
            break
    return best or game["history"][0]


# ---------------------------------------------------------------------------

def analyse(pool, games):
    rows = []
    sheet_date = pool["sheet_date"]
    mnf = pool.get("mnf") or {}
    mnf_h, mnf_a = _key(mnf.get("home", "")), _key(mnf.get("away", ""))

    for entry in pool["lines"]:
        g = match_game(entry, games)
        if g is None:
            rows.append({"matchup": f"{entry['away']} @ {entry['home']}",
                         "error": "no market data for this game"})
            continue

        pool_spread = float(entry["home_spread"])
        at_sheet = market_on(g, sheet_date)["consensus"]
        now = g["current"]["consensus"]

        # Line value, from each side's point of view, in points.
        # Home side benefits when the market has moved toward the home team
        # (more negative) relative to the pool number.
        home_value = pool_spread - now
        side = "home" if home_value > 0 else "away"
        value = abs(home_value)

        win, push = cover_probability(pool_spread, now, side)
        team = g["home_team"] if side == "home" else g["away_team"]

        crossed = None
        for k in (3, 7):
            lo, hi = sorted((abs(pool_spread), abs(now)))
            if lo < k <= hi:
                crossed = k

        rows.append({
            "game_id": g["game_id"],
            "week": g["week"],
            "kickoff": g["commence_time"],
            "matchup": f"{g['away_team']} @ {g['home_team']}",
            "pool_line": pool_spread,
            "market_at_sheet": at_sheet,
            "market_now": now,
            "drift_since_sheet": round(now - at_sheet, 1),
            "pick": team,
            "pick_side": side,
            "pick_line": pool_spread if side == "home" else -pool_spread,
            "line_value": round(value, 1),
            "crossed_key": crossed,
            "win_prob": round(win, 4),
            "push_prob": round(push, 4),
            "is_mnf": (_key(g["home_team"]) == mnf_h and _key(g["away_team"]) == mnf_a)
                      or (mnf_h and _key(g["home_team"]).endswith(mnf_h.split()[-1] if mnf_h else "~")
                          and _key(g["away_team"]).endswith(mnf_a.split()[-1] if mnf_a else "~")),
        })

    ok = [r for r in rows if "error" not in r]
    ok.sort(key=lambda r: -r["win_prob"])
    return ok, [r for r in rows if "error" in r]


def perfect_five(picks):
    p = 1.0
    for r in picks:
        p *= r["win_prob"]
    return p


def fmt_line(v):
    return "PK" if v == 0 else f"{v:+.1f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--games", default="data/games.json")
    ap.add_argument("--context", default="data/context.json")
    ap.add_argument("--field", type=int, default=None,
                    help="override field size for the split-pot math")
    args = ap.parse_args()

    with open(args.pool) as f:
        pool = json.load(f)
    with open(args.games) as f:
        games = json.load(f)

    context = {}
    if os.path.exists(args.context):
        with open(args.context) as f:
            context = (json.load(f) or {}).get("games", {})

    field = args.field or pool.get("field_size") or 50
    rows, bad = analyse(pool, games)

    print(f"\nWRG Week {pool['week']} — sheet dated {pool['sheet_date']}, "
          f"market as of {datetime.utcnow().date()}, field ~{field}\n")
    print(f"{'':2} {'MATCHUP':38} {'POOL':>6} {'MKT':>6} {'VALUE':>6} {'KEY':>4} {'PICK':24} {'WIN%':>6}")
    print("-" * 100)
    for i, r in enumerate(rows, 1):
        star = "M" if r["is_mnf"] else " "
        key = str(r["crossed_key"]) if r["crossed_key"] else ""
        print(f"{star}{i:>2} {r['matchup']:38} {fmt_line(r['pool_line']):>6} "
              f"{fmt_line(r['market_now']):>6} {r['line_value']:>6.1f} {key:>4} "
              f"{r['pick'] + ' ' + fmt_line(r['pick_line']):24} {r['win_prob']*100:>5.1f}")

    for r in bad:
        print(f"  !! {r['matchup']}: {r['error']}")

    mnf = [r for r in rows if r["is_mnf"]]
    non_mnf = [r for r in rows if not r["is_mnf"]]

    print("\n" + "=" * 100)
    if not mnf:
        print("No Monday night game matched the sheet — set `mnf` in the pool file.")
        return
    m = mnf[0]

    # A game with no line value is a coinflip at best and a 47% shot if it sits on
    # a whole number, because a push loses. Never let one into the slate.
    with_edge = [r for r in non_mnf if r["line_value"] > 0]
    no_edge = [r for r in non_mnf if r["line_value"] <= 0]
    slate = with_edge[:4] + [m]
    p5 = perfect_five(slate)

    if len(with_edge) < 4:
        print(f"\n  !! Only {len(with_edge)} non-Monday games have any line value. "
              f"Filling a slate from\n     zero-edge games is how you turn a 6% week "
              f"into a 3% one. Consider sitting out\n     or waiting for more movement "
              f"before the Saturday deadline.")
        slate = (with_edge + no_edge)[:4] + [m]
        p5 = perfect_five(slate)

    print(f"\nSTRAIGHT-PROBABILITY SLATE (maximise P(5-0), ignore the field)\n")
    for r in slate:
        tag = " [MNF]" if r["is_mnf"] else ""
        print(f"  {r['pick'] + ' ' + fmt_line(r['pick_line']):30} "
              f"{r['win_prob']*100:5.1f}%   {r['line_value']:+.1f} pts of value{tag}")
    # Context is shown, never folded into the probability. The line value is
    # measured; an injury's effect on a spread is not, and pretending otherwise
    # would put a made-up number in front of a real one.
    flagged = []
    for r in slate:
        c = context.get(r["game_id"])
        if not c:
            continue
        bits = []
        for side in ("home", "away"):
            for pl in (c.get("injuries") or {}).get(side, []):
                if pl.get("tier") == 1 or pl.get("status") in ("Out", "Doubtful"):
                    bits.append(f"{pl['position']} {pl['name']} {pl['status'].lower()}")
        w = c.get("weather") or {}
        if w.get("windy"):
            bits.append(f"wind {round(w.get('wind_mph') or 0)} mph")
        if bits:
            flagged.append((r["pick"], bits))

    if flagged:
        print(f"\n  CHECK BEFORE SUBMITTING — not priced into the numbers above:")
        for pick, bits in flagged:
            print(f"    {pick:28} {'; '.join(bits)}")
        print(f"    If any of this broke after the sheet went out, the market has it and")
        print(f"    the pool line does not — which is the edge. If it broke before, it is")
        print(f"    already in both numbers and changes nothing.")

    print(f"\n  P(all five)      {p5*100:5.2f}%")
    print(f"  Expected 5-0s in a {field}-player field, if everyone were this sharp: "
          f"{p5*field:.1f}")

    # The MNF differentiation trade.
    other = m["market_now"]
    flip_side = "away" if m["pick_side"] == "home" else "home"
    fw, _ = cover_probability(m["pool_line"], other, flip_side)
    flip_team = m["matchup"].split(" @ ")[0 if flip_side == "away" else 1]
    p5_flip = perfect_five(non_mnf[:4]) * fw

    cost = (m["win_prob"] - fw) * 100

    print(f"\nMONDAY NIGHT — THE DIFFERENTIATION TRADE\n")
    print(f"  Model's side   : {m['pick']} {fmt_line(m['pick_line'])}  "
          f"({m['win_prob']*100:.1f}%, {m['line_value']:+.1f} pts of value)")
    print(f"  Other side     : {flip_team} {fmt_line(-m['pick_line'])}  ({fw*100:.1f}%)")
    print(f"  Cost of flipping: {cost:.1f} points of win probability "
          f"({p5*100:.2f}% -> {p5_flip*100:.2f}% for the week)")

    fav_is_home = m["market_now"] < 0
    fav_team = m["matchup"].split(" @ ")[1 if fav_is_home else 0]
    model_on_dog = m["pick"] != fav_team

    print()
    if model_on_dog:
        print(f"  VERDICT: take {m['pick']}, and you get the differentiation free.")
        print(f"  The line value points at the underdog, which is also the side a room")
        print(f"  full of people picking with their gut tends to avoid. No trade needed —")
        print(f"  the value pick and the unpopular pick are the same pick this week.")
    elif cost <= 4.0:
        print(f"  VERDICT: consider flipping to {flip_team}.")
        print(f"  Costs only {cost:.1f} points of win probability, and every entrant picks")
        print(f"  this game, so it is where the field bunches hardest. If the room leans")
        print(f"  ~65/35 to {fav_team}, the other side roughly halves who you split with.")
    else:
        print(f"  VERDICT: stay on {m['pick']}. Flipping costs {cost:.1f} points, which is")
        print(f"  too much to pay for differentiation. Below about 4 points it is worth it;")
        print(f"  this is not close enough.")

    print(f"\n  The 65/35 split is an assumption, not a measurement. Log who wins and what")
    print(f"  they picked each week and it stops being one by midseason.\n")


if __name__ == "__main__":
    main()
