#!/usr/bin/env python3
"""
Select the week's five picks and write the reasoning behind each.

  python scripts/make_picks.py --pool pool/week01.json [--out data/picks.json]

Four games plus the mandatory Monday nighter, chosen to maximise the probability
of going 5-0 against the pool's own Thursday line sheet.

WHAT THE REASONING IS ALLOWED TO SAY
------------------------------------
Every sentence must rest on something measured. Three things qualify:

  1. Points of price. The gap between the pool's frozen line and the current
     market consensus. A hard number, in points.
  2. Key-number position. Whether the pick crosses or lands on 3 or 7, and what
     that is worth, computed from the same margin model that produces the
     probability.
  3. Dated facts. An injury designation with the date it changed; a wind forecast
     in mph. Reported as facts, never as a quantified effect on the spread.

Nothing else is permitted. Not team form, not ATS records, not rest, not
"due for a win", and never a claim that an injury is worth N points -- that
number is not measured anywhere in this system, and inventing it would put a
fabricated figure next to three real ones.

When nothing supports a pick beyond the price, the reasoning says exactly that.
A pick whose only justification is that the price is wrong is a good pick; a
pick dressed in a story is a worse one.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pool_edge import (  # noqa: E402
    analyse, cover_probability, perfect_five, _margin_pmf, fmt_line,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def key_number_share(k):
    """Model's own P(final margin is exactly k, either direction), for a typical
    line. Quoted so the reasoning cites the same distribution as the probability."""
    pmf = _margin_pmf(-3.0)
    return (pmf.get(k, 0) + pmf.get(-k, 0)) * 100


def context_facts(row, ctx, sheet_date):
    """Dated, factual context for one game. Nothing quantified, nothing inferred."""
    facts = []
    c = ctx.get(row["game_id"])
    if not c:
        return facts, False
    after_sheet = False
    for side in ("home", "away"):
        for p in (c.get("injuries") or {}).get(side, []):
            if p.get("tier") != 1 and p.get("status") not in ("Out", "Doubtful"):
                continue
            when = p.get("changed_on")
            fresh = bool(when and sheet_date and when > sheet_date)
            after_sheet = after_sheet or fresh
            facts.append(
                f"{p['position']} {p['name']} is {p['status'].lower()}"
                + (f", changed {when} — after the sheet was set" if fresh
                   else (f" (since {when})" if when else ""))
            )
    w = c.get("weather") or {}
    if w.get("windy"):
        facts.append(
            f"forecast is {round(w['wind_mph'])} mph wind at kickoff"
            + (f", gusting {round(w['gust_mph'])}" if w.get("gust_mph") else "")
        )
    return facts, after_sheet


def reason_for(row, ctx, sheet_date):
    """Build the reasoning as a list of claims, each with what backs it."""
    claims = []
    value = row["line_value"]
    line = fmt_line(row["pick_line"])
    mkt = fmt_line(row["market_now"] if row["pick_side"] == "home" else -row["market_now"])

    if value > 0:
        claims.append({
            "kind": "price",
            "text": f"The pool has this at {line} while the market has moved to {mkt}. "
                    f"That is {value:.1f} point{'s' if value != 1 else ''} of price you "
                    f"are getting for free, because the sheet froze Thursday and the "
                    f"market did not.",
        })
    else:
        claims.append({
            "kind": "price",
            "text": f"No price edge — the pool number and the market agree at {line}. "
                    f"This is in the slate only because too few games carried value "
                    f"this week.",
        })

    k = row.get("crossed_key")
    if k:
        claims.append({
            "kind": "key_number",
            "text": f"The move crossed {k}. About {key_number_share(k):.1f}% of NFL games "
                    f"finish on exactly {k}, so points either side of it are worth more "
                    f"than points anywhere else on the board.",
        })
    if float(row["pick_line"]).is_integer() and row["push_prob"] > 0:
        claims.append({
            "kind": "push_risk",
            "text": f"It sits on a whole number, so a push is live at "
                    f"{row['push_prob']*100:.1f}% — and a push loses in this pool. That "
                    f"is already subtracted from the probability below.",
        })

    facts, after_sheet = context_facts(row, ctx, sheet_date)
    if facts:
        claims.append({
            "kind": "context",
            "text": ("Worth knowing: " + "; ".join(facts) + ". "
                     + ("That broke after the sheet was set, so the market has it and the "
                        "pool line does not." if after_sheet else
                        "That was known before the sheet went out, so it is priced into "
                        "both numbers and changes nothing.")),
        })
    else:
        claims.append({
            "kind": "context",
            "text": "No injury or weather news attaches to this one. The edge is the "
                    "price and nothing else, which is the most reliable kind this "
                    "system finds.",
        })

    claims.append({
        "kind": "probability",
        "text": f"{row['win_prob']*100:.1f}% to cover, from a margin model calibrated to "
                f"published NFL scoring frequencies. Treat the ranking as more "
                f"trustworthy than the decimal.",
    })
    return claims


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--games", default=os.path.join(ROOT, "data", "games.json"))
    ap.add_argument("--context", default=os.path.join(ROOT, "data", "context.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "picks.json"))
    ap.add_argument("--final", action="store_true",
                    help="mark this slate final — set only by the run that lands "
                         "before the pool deadline")
    args = ap.parse_args()

    with open(args.pool) as f:
        pool = json.load(f)
    with open(args.games) as f:
        games = json.load(f)
    ctx = {}
    if os.path.exists(args.context):
        with open(args.context) as f:
            ctx = (json.load(f) or {}).get("games", {})

    rows, bad = analyse(pool, games)
    sheet_date = pool.get("sheet_date")
    field = pool.get("field_size") or 50

    mnf = [r for r in rows if r["is_mnf"]]
    non_mnf = [r for r in rows if not r["is_mnf"]]
    with_edge = [r for r in non_mnf if r["line_value"] > 0]
    thin = len(with_edge) < 4
    four = (with_edge + [r for r in non_mnf if r["line_value"] <= 0])[:4]

    if not mnf:
        sys.exit("No Monday night game matched the sheet — set `mnf` in the pool file.")
    m = mnf[0]
    slate = four + [m]

    # The differentiation read on the mandatory game.
    flip_side = "away" if m["pick_side"] == "home" else "home"
    fw, _ = cover_probability(m["pool_line"], m["market_now"], flip_side)
    fav_is_home = m["market_now"] < 0
    fav_team = m["matchup"].split(" @ ")[1 if fav_is_home else 0]
    cost = (m["win_prob"] - fw) * 100
    if m["pick"] != fav_team:
        mnf_note = (f"The price points at the underdog, which is also the side a room of "
                    f"{field} tends to avoid. The value pick and the unpopular pick are "
                    f"the same pick — take it and stop thinking about it.")
    elif cost <= 4.0:
        other = m["matchup"].split(" @ ")[0 if fav_is_home else 1]
        mnf_note = (f"Flipping to {other} costs {cost:.1f} points of win probability. Every "
                    f"entrant picks this game, so it is where the field bunches hardest; "
                    f"in a {field}-player pool that trade is usually worth making.")
    else:
        mnf_note = (f"Flipping to the unpopular side would cost {cost:.1f} points of win "
                    f"probability. Too much to pay for differentiation — stay here.")

    # Picks refresh on every collection run, so most of the week the board is
    # showing a slate that will still change. Saying so is the guard against
    # submitting Wednesday's answer to Saturday's question -- the two days of
    # movement between the sheet and the deadline ARE the edge, and acting early
    # is how it gets thrown away.
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "final" if args.final else "provisional",
        "week": pool.get("week"),
        "sheet_date": sheet_date,
        "field_size": field,
        "thin_week": thin,
        "games_with_edge": len(with_edge),
        "p_perfect": round(perfect_five(slate), 5),
        "mnf_note": mnf_note,
        "unmatched": [r["matchup"] for r in bad],
        "picks": [{
            "game_id": r["game_id"],
            "matchup": r["matchup"],
            "kickoff": r["kickoff"],
            "pick": r["pick"],
            "pick_line": r["pick_line"],
            "pool_line": r["pool_line"],
            "market_now": r["market_now"],
            "line_value": r["line_value"],
            "crossed_key": r["crossed_key"],
            "win_prob": r["win_prob"],
            "push_prob": r["push_prob"],
            "is_mnf": r["is_mnf"],
            "reasoning": reason_for(r, ctx, sheet_date),
        } for r in slate],
    }

    with open(args.out, "w") as f:
        json.dump(payload, f, indent=1)

    state = "FINAL" if args.final else "PROVISIONAL — will change before the deadline"
    print(f"\nWEEK {pool.get('week')} — five picks  [{state}]  "
          f"(P(5-0) {payload['p_perfect']*100:.2f}%, field ~{field})\n")
    for p in payload["picks"]:
        tag = " [MONDAY NIGHT]" if p["is_mnf"] else ""
        print(f"  {p['pick']} {fmt_line(p['pick_line'])}{tag}")
        print(f"    {p['win_prob']*100:.1f}% · {p['line_value']:+.1f} pts of price"
              + (f" · crossed {p['crossed_key']}" if p["crossed_key"] else ""))
        for c in p["reasoning"]:
            print(f"      - {c['text']}")
        print()
    print(f"  Monday night: {mnf_note}")
    if thin:
        print(f"\n  !! Only {len(with_edge)} games carried price value this week. A slate "
              f"padded with\n     no-edge games is a materially worse bet than a normal "
              f"week — consider it.")
    if not args.final:
        print(f"\n  This slate is PROVISIONAL. It refreshes every collection run and the")
        print(f"  numbers behind it are still moving. The gap between the sheet and the")
        print(f"  Saturday deadline is the edge — submitting early spends it for nothing.")
    print(f"\n  wrote {args.out}")


if __name__ == "__main__":
    main()
