#!/usr/bin/env python3
"""Build the engine's five picks from a pool sheet and the current market.

REIMPLEMENTATION, 2026-09-09. The original make_picks.py was written 2026-09-08
and never committed; this is a rebuild from claude/saturday-picks-runbook.md,
claude/pool-strategy.md and the engine notes in claude/ledger-runbook.md.

  python3 make_picks.py --pool pool-week-01.json --games games_fixed.json \
      [--context context.json] [--notes notes.json] [--prev prev_engine.json] \
      --out picks.json [--final]

Rules this file enforces, each of them from a runbook and not from taste:

  * The sheet is validated first, every time. validate_sheet.py's correlation
    check is the only thing standing between a transcription sign error and five
    confident, precisely-reasoned, exactly wrong picks. --skip-validation exists
    for a sheet checked by hand and must never appear in a scheduled run.
  * Three-book floor. A pick's edge is one number minus another, and the
    subtraction only means something if the second number is really the market.
    The mandatory Monday game is the single exception: priced anyway, warned.
  * Five picks = four chosen + the mandatory Monday night game. The MNF slot is
    never backfilled with an ordinary game; that produces an entry the pool does
    not accept while still looking like five picks.
  * Reasoning may make three claims and no others: points of price, key-number
    position, and dated facts that POSTDATE the sheet. Anything older is already
    inside both numbers. No claim that an injury is worth N points -- that number
    is not measured anywhere in this system.

THIS FILE REPLACED the 2026-09-08 original in the repo on 2026-10-13, together
with pool_edge.py and validate_sheet.py. The original had no post-deadline lock
at all and imported five names from pool_edge that this engine does not define,
so the three had to migrate as one unit.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pool_edge
from validate_sheet import match as match_sheet

BOOKS_FLOOR = 3
REQUIRED = 5
CHOSEN = 4

# A judgement threshold, not a measured one. The strategy doc says early-window
# games are "only worth it when the line gap is unusually large", without saying
# how large. This is where that line is drawn; it is an assumption.
EARLY_GAME_MIN_VALUE = 1.5

# Fewer than this many games carrying any price makes it a thin week, and the
# runbook says to report that plainly rather than dress up a padded slate.
THIN_WEEK_BELOW = 4


def deadline_utc(slate):
    """Noon Arizona (19:00 UTC) on the Saturday before the week's main slate."""
    kicks = sorted(datetime.fromisoformat(r["commence_time"].replace("Z", "+00:00"))
                   for r in slate)
    main = kicks[len(kicks) // 2]                      # the Sunday block
    d = main.date()
    while d.weekday() != 5:                            # 5 = Saturday
        d -= timedelta(days=1)
    return datetime(d.year, d.month, d.day, 19, 0, tzinfo=timezone.utc)


def dated_facts(game_id, sheet_date, context, notes):
    """Facts that postdate the sheet. Anything older is priced into both numbers."""
    out = []
    ctx = (context or {}).get(game_id) or {}
    for item in ctx.get("injuries", []):
        if item.get("changed_on") and item["changed_on"] > sheet_date:
            # FIXED 2026-10-13: this read item.get("player"), which no producer
            # has ever written. The collector's context.py emits "name" (line
            # 169) and so does rebuild_injuries.py, so every injury fact built
            # here rendered as "None (QB) Out as of ...". It could only surface
            # once ESPN started answering again on 2026-10-01, which is why it
            # survived a month. "player" is still accepted as a fallback in case
            # an older context.json is ever replayed.
            who = item.get("name") or item.get("player")
            out.append(f"{who} ({item.get('position')}) "
                       f"{item.get('status')} as of {item['changed_on']}")
    w = ctx.get("weather") or {}
    if w.get("wind_mph") and w["wind_mph"] >= 15 and (w.get("as_of") or "") > sheet_date:
        out.append(f"wind {w['wind_mph']} mph at kickoff (forecast {w['as_of']})")
    for n in (notes or {}).get(game_id, []):
        if n.get("date", "") > sheet_date and "none_found" not in (n.get("tags") or []):
            out.append(n["text"])
    return out


def bought_outcome(row, side):
    """Exactly which final margins the pool's number wins that the market's does not.

    This is line value restated as the thing it actually buys. Holding -3.5
    against a market of -4.0 wins games decided by exactly 4, which pushed (and
    so lost) at the market number. Holding -3.5 against -4.5 wins the same
    margin with no push involved. Both come out of the same margin model that
    produces the probability, so this is a key-number claim under the runbook,
    not a fourth kind of claim.

    -> (sorted list of |margin| values gained, total probability) or None.
    """
    mkt, pool = float(row["market_line"]), float(row["pool_line"])
    if abs(mkt - pool) < 0.01:
        return None
    pmf = pool_edge.margin_pmf(pool_edge.calibrate_mu(-mkt))
    if side == "home":
        gained = [m for m in pmf if -pool < m <= -mkt]
    else:
        gained = [m for m in pmf if -mkt <= m < -pool]
    gained = [m for m in gained if pmf[m] > 1e-6]
    if not gained:
        return None
    return sorted({abs(m) for m in gained}), sum(pmf[m] for m in gained)


def reason_for(row, pick, facts):
    """The three permitted claims, and nothing else."""
    bits = []
    team = row["home_team"] if pick["side"] == "home" else row["away_team"]
    held = row["pool_line"] if pick["side"] == "home" else -row["pool_line"]
    mkt = row["market_line"] if pick["side"] == "home" else -row["market_line"]

    if abs(pick["line_value"]) < 0.01:
        bits.append(f"No price: the pool has {team} at {held:+.1f} and so does the market.")
    else:
        bits.append(f"{team} at {held:+.1f} against a market of {mkt:+.1f} — "
                    f"{pick['line_value']:+.1f} points of price.")

    if pick["push"] > 0.005:
        bits.append(f"The number is whole, so the {pick['push'] * 100:.1f}% chance of landing "
                    f"exactly on it is a loss, not a refund.")
    if abs(pick["line_value"]) >= 0.01:
        got = bought_outcome(row, pick["side"])
        if got:
            margins, p = got
            which = (f"exactly {margins[0]}" if len(margins) == 1
                     else "exactly " + ", ".join(str(m) for m in margins[:-1]) +
                          f" or {margins[-1]}")
            bits.append(f"What the price buys, concretely: games decided by {which} — "
                        f"a loss at the market's number, a win at the pool's. "
                        f"The model puts that at {p * 100:.1f}%.")

    if facts:
        bits.append("Since the sheet: " + "; ".join(facts[:2]) + ".")
    elif abs(pick["line_value"]) < 0.01:
        # `on_key` comes straight out of pool_edge.best_side and had never been
        # read here. Without it this branch asserted "not sitting on 3 or 7" on
        # EVERY no-price leg -- including a mandatory Monday night game that is
        # sitting on one, which is how Week 2 produced a pick reading "not
        # sitting on 3 or 7" about a line of exactly 7.0, inches from the 47.9%
        # the key number itself had caused. Key-number position is one of the
        # three claims the runbook permits; stating it backwards is worse than
        # not stating it.
        if pick.get("on_key"):
            why = ("the Monday night slot is mandatory and this is the game in it"
                   if pick.get("is_mnf") else "the slate ran out of games carrying one")
            bits.append(f"It sits ON the key number {abs(held):.0f} with no price, "
                        f"which is why the model prices it below an even chance. "
                        f"It is here because {why}.")
        else:
            bits.append("It ranks here only because it is not sitting on 3 or 7.")
    return " ".join(bits)


def build(pool, games, context, notes, final, skip_validation):
    sheet_date = pool.get("sheet_date") or ""
    rows, unmatched = match_sheet(pool, games)
    rows = [r for r in rows if r["market_line"] is not None]
    if not rows:
        raise SystemExit("no sheet line matched a priced game")

    dl = deadline_utc(rows)
    now = datetime.now(timezone.utc)

    mnf_name = None
    if pool.get("mnf"):
        mnf_name = (pool["mnf"].get("away"), pool["mnf"].get("home"))
    by_kick = sorted(rows, key=lambda r: r["commence_time"])
    mnf_id = None
    for r in by_kick:
        if mnf_name and (r["away_team"], r["home_team"]) == mnf_name:
            mnf_id = r["game_id"]
    if mnf_id is None:
        mnf_id = by_kick[-1]["game_id"]                 # the week's last kickoff

    priced = []
    for r in rows:
        p = pool_edge.best_side(r["market_line"], r["pool_line"])
        kick = datetime.fromisoformat(r["commence_time"].replace("Z", "+00:00"))
        cand = dict(r)
        cand.update(p)
        cand["is_mnf"] = r["game_id"] == mnf_id
        cand["kicked_off"] = kick <= now
        cand["early_window"] = kick <= dl and not cand["is_mnf"]
        cand["thin_books"] = (r["n_books"] or 0) < BOOKS_FLOOR
        cand["facts"] = dated_facts(r["game_id"], sheet_date, context, notes)
        priced.append(cand)

    with_price = [c for c in priced if abs(c["line_value"]) >= 0.01]
    thin_week = len(with_price) < THIN_WEEK_BELOW

    mnf = [c for c in priced if c["is_mnf"]][0]
    warnings = []
    if mnf["thin_books"]:
        warnings.append(f"The Monday night game is priced by {mnf['n_books']} book(s), under the "
                        f"{BOOKS_FLOOR}-book floor. It is mandatory, so it is priced anyway.")

    pool_of = []
    for c in priced:
        if c["is_mnf"] or c["kicked_off"]:
            continue
        if c["thin_books"]:
            continue                                    # under the floor is not a candidate
        if c["early_window"] and abs(c["line_value"]) < EARLY_GAME_MIN_VALUE:
            continue                                    # forfeits two days of information
        pool_of.append(c)

    # Deterministic to the last field: the page reruns this same ordering in
    # JavaScript, and a tiebreak that depended on input order would let the two
    # pick different games from a 7-way tie without either being wrong.
    pool_of.sort(key=lambda c: (-c["win_prob"], -abs(c["line_value"]),
                                c["commence_time"], c["game_id"]))
    chosen = pool_of[:CHOSEN]

    excluded_thin = [c for c in priced if c["thin_books"] and not c["is_mnf"]]
    excluded_early = [c for c in priced
                      if c["early_window"] and not c["kicked_off"]
                      and abs(c["line_value"]) < EARLY_GAME_MIN_VALUE]
    if excluded_early:
        warnings.append(
            "Skipped the early window (" +
            ", ".join(f"{c['away_team'].split()[-1]} at {c['home_team'].split()[-1]}"
                      for c in excluded_early) +
            f"): those must be submitted before kickoff, which spends two days of information, "
            f"and none carries the {EARLY_GAME_MIN_VALUE:+.1f} points that would justify it.")
    if excluded_thin:
        warnings.append(f"{len(excluded_thin)} game(s) excluded under the {BOOKS_FLOOR}-book floor.")
    if len(chosen) < CHOSEN:
        warnings.append(f"Only {len(chosen)} eligible games for {CHOSEN} slots.")
    if thin_week:
        warnings.append(f"Thin week: {len(with_price)} of {len(rows)} games carry any price at all.")

    # A tie at the cut line means the last slot was decided by sort order, not by
    # the model. Saying so is the difference between a ranking and a coin flip
    # wearing a ranking's clothes.
    if chosen:
        cut = chosen[-1]["win_prob"]
        tied = [c for c in pool_of if abs(c["win_prob"] - cut) < 5e-4]
        if len(tied) > len([c for c in chosen if abs(c["win_prob"] - cut) < 5e-4]):
            warnings.append(
                f"{len(tied)} games are tied at {cut * 100:.1f}% for the last "
                f"{len([c for c in chosen if abs(c['win_prob'] - cut) < 5e-4])} slot(s): " +
                ", ".join(f"{c['away_team'].split()[-1]} at {c['home_team'].split()[-1]}"
                          for c in tied[:8]) +
                ". The model does not separate them; the order is the tiebreak, not a judgement.")

    def entry(c):
        return {
            "game_id": c["game_id"],
            "matchup": f"{c['away_team']} @ {c['home_team']}",
            "team": c["home_team"] if c["side"] == "home" else c["away_team"],
            "side": c["side"],
            # pool_line and market_line stay quoted from the HOME side, because that
            # is what the board and the ledger expect. held_line is the same number
            # from the side actually picked -- the one a human reads.
            "held_line": c["pool_line"] if c["side"] == "home" else -c["pool_line"],
            "pool_line": c["pool_line"],
            "market_line": c["market_line"],
            "line_value": c["line_value"] + 0.0 or 0.0,
            "win_prob": round(c["win_prob"], 4),
            "push_prob": round(c["push"], 4),
            "n_books": c["n_books"],
            "is_mnf": c["is_mnf"],
            "kick": c["commence_time"],
            "reasoning": reason_for(c, c, c["facts"]),
        }

    engine = [entry(c) for c in chosen] + [entry(mnf)]

    other = "away" if mnf["side"] == "home" else "home"
    other_p = pool_edge.price(mnf["market_line"], mnf["pool_line"])
    mnf_note = {
        "engine_side": mnf["side"],
        "engine_team": mnf["home_team"] if mnf["side"] == "home" else mnf["away_team"],
        "engine_win_prob": round(mnf["win_prob"], 4),
        "other_team": mnf["home_team"] if other == "home" else mnf["away_team"],
        "other_win_prob": round(other_p["home_win"] if other == "home" else other_p["away_win"], 4),
        "level": abs(mnf["win_prob"] - (other_p["home_win"] if other == "home"
                                        else other_p["away_win"])) < 5e-4,
        "text": ("Every entrant picks this game, so it is the field's biggest correlation point. "
                 "When the value side is also the unpopular side you get differentiation free; "
                 "when it is not, taking the unpopular side is worth roughly 4 points of win "
                 "probability in a 50+ field, and no more. The 65/35 split that figure assumes "
                 "is an assumption, not a measurement, and which way to go is your call."),
    }

    return {
        "week": pool.get("week"),
        "sheet_date": sheet_date,
        "state": "final" if final else "provisional",
        "market_date": max((g["current"]["date"] for g in games.values()
                            if g.get("current", {}).get("date")), default=None),
        "generated_at": now.isoformat(timespec="seconds"),
        "engine": engine,
        "thin_week": thin_week,
        "warnings": warnings,
        "mnf_note": mnf_note,
        "unmatched": unmatched,
        "validated": not skip_validation,
        "model": {"sigma": pool_edge.SIGMA, "key_mass": pool_edge.KEY_MASS,
                  "note": "Reimplementation of 2026-09-09; constants are from public NFL "
                          "margin data, not from this pool's results."},
        "all_priced": [entry(c) for c in sorted(priced, key=lambda c: c["commence_time"])],
    }


def diff(prev, cur):
    """What changed since the last slate, and the measured reason it changed."""
    if not prev:
        return []
    pmap = {p["game_id"]: p for p in prev.get("engine", [])}
    cmap = {p["game_id"]: p for p in cur.get("engine", [])}
    changes = []

    for gid, c in cmap.items():
        p = pmap.get(gid)
        if p is None:
            changes.append({"type": "added", "game_id": gid, "matchup": c["matchup"],
                            "text": f"IN — {c['team']} {c['held_line']:+.1f}. "
                                    f"{c['line_value']:+.1f} points of price at "
                                    f"{c['win_prob'] * 100:.1f}%."})
        elif p["side"] != c["side"]:
            changes.append({"type": "side_flip", "game_id": gid, "matchup": c["matchup"],
                            "text": f"SIDE FLIPPED — was {p['team']}, now {c['team']}. "
                                    f"The market moved {p['market_line']:+.1f} → "
                                    f"{c['market_line']:+.1f}, which put the pool's number on "
                                    f"the other side of it."})
        elif abs(p["market_line"] - c["market_line"]) >= 0.01:
            changes.append({"type": "repriced", "game_id": gid, "matchup": c["matchup"],
                            "text": f"held — {c['team']}, but the market moved "
                                    f"{p['market_line']:+.1f} → {c['market_line']:+.1f}, so the "
                                    f"price is now {c['line_value']:+.1f} "
                                    f"(was {p['line_value']:+.1f}) and the pick prices at "
                                    f"{c['win_prob'] * 100:.1f}% (was {p['win_prob'] * 100:.1f}%)."})

    cur_all = {e["game_id"]: e for e in cur.get("all_priced", [])}
    for gid, p in pmap.items():
        if gid in cmap:
            continue
        now_row = cur_all.get(gid)
        why = ""
        if now_row:
            if abs(now_row["market_line"] - p["market_line"]) >= 0.01:
                why = (f" The market moved {p['market_line']:+.1f} → "
                       f"{now_row['market_line']:+.1f}, cutting the price from "
                       f"{p['line_value']:+.1f} to {now_row['line_value']:+.1f}.")
            else:
                why = (" Its number did not move; another game overtook it.")
        changes.append({"type": "dropped", "game_id": gid, "matchup": p["matchup"],
                        "text": f"OUT — {p['team']} {p['held_line']:+.1f}.{why}"})
    return changes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--games", required=True)
    ap.add_argument("--context")
    ap.add_argument("--notes")
    ap.add_argument("--prev")
    ap.add_argument("--prev-doc", dest="prev_doc",
                    help="the week doc currently in the datastore. Once it is "
                         "final or carries Austin's picks, this run must not "
                         "rewrite the slate.")
    ap.add_argument("--out", required=True)
    ap.add_argument("--final", action="store_true")
    ap.add_argument("--skip-validation", action="store_true")
    args = ap.parse_args()

    pool = json.load(open(args.pool))
    games = json.load(open(args.games))
    context = json.load(open(args.context)) if args.context and os.path.exists(args.context) else {}
    notes = json.load(open(args.notes)) if args.notes and os.path.exists(args.notes) else {}

    if not args.skip_validation:
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                         "validate_sheet.py"),
                            "--pool", args.pool, "--games", args.games],
                           capture_output=True, text=True)
        print(r.stdout.strip())
        if r.returncode != 0:
            sys.exit("sheet REJECTED by validate_sheet.py — no picks built")

    cur = build(pool, games, context, notes, args.final, args.skip_validation)

    # ---- the lock -------------------------------------------------------
    # The daily run keeps going after Saturday's deadline, which is what makes
    # closing-line movement visible. But a week that is FINAL, or that already
    # carries Austin's submitted five, is a record -- and a record you can
    # rewrite afterwards is not a record. So past that point this stops
    # producing a new slate and starts watching the one that was submitted.
    locked_doc = (json.load(open(args.prev_doc))
                  if args.prev_doc and os.path.exists(args.prev_doc) else None)
    if locked_doc and (locked_doc.get("state") == "final"
                       or (locked_doc.get("austin") or [])):
        why = ("the week is marked final" if locked_doc.get("state") == "final"
               else "Austin's picks are recorded")
        priced_now = {e["game_id"]: e for e in cur["all_priced"]}
        watch = []
        for e in locked_doc.get("engine", []):
            now = priced_now.get(e["game_id"])
            if not now:
                continue
            moved = round(now["market_line"] - e["market_line"], 2)
            watch.append({
                "game_id": e["game_id"], "matchup": e["matchup"], "team": e["team"],
                "side": e["side"], "held_line": e.get("held_line", e["pool_line"]),
                "market_when_locked": e["market_line"], "market_now": now["market_line"],
                "market_moved": moved,
                "win_prob_when_locked": e["win_prob"],
                "win_prob_now": (now["win_prob"] if now["side"] == e["side"]
                                 else round(1 - now["win_prob"] - now["push_prob"], 4)),
            })
        cur["engine"] = locked_doc["engine"]
        cur["state"] = locked_doc.get("state", "final")
        cur["locked"] = True
        cur["locked_reason"] = why
        cur["closing_watch"] = watch
        cur["changes"] = []
        cur["warnings"] = [f"LOCKED: {why}, so the slate was not rebuilt. The market numbers "
                           f"below are current; the picks are the ones on record."]
        cur["write_fields"] = ["slate", "market_date", "closing_watch", "locked"]
    else:
        cur["locked"] = False
        prev = (json.load(open(args.prev))
                if args.prev and os.path.exists(args.prev) else None)
        cur["changes"] = diff(prev, cur)
        cur["write_fields"] = ["slate", "market_date", "state", "engine", "thin_week",
                               "warnings", "mnf_note", "changes", "model"]

    json.dump(cur, open(args.out, "w"), indent=1)

    if cur.get("locked"):
        print(f"\n*** LOCKED — {cur['locked_reason']}. The slate on record was NOT rebuilt. ***")
    print(f"\nstate={cur['state']}  market={cur['market_date']}  "
          f"thin_week={cur['thin_week']}")
    for w in cur["warnings"]:
        print("  ! " + w)
    print()
    for i, e in enumerate(cur["engine"]):
        slot = "MNF" if e["is_mnf"] else str(i + 1)
        print(f"{slot:>3}  {e['team']:<24} {e['held_line']:+5.1f}  "
              f"value {e['line_value']:+4.1f}  {e['win_prob'] * 100:5.1f}%  "
              f"{e['matchup']}")
        print(f"     {e['reasoning']}")
    if cur.get("closing_watch"):
        print("\nhow the submitted five have moved since they were locked:")
        for w in cur["closing_watch"]:
            arrow = "no move" if abs(w["market_moved"]) < 0.01 else (
                f"{w['market_when_locked']:+.1f} -> {w['market_now']:+.1f}")
            print(f"  {w['team']:<24} {w['held_line']:+5.1f}  market {arrow:<18} "
                  f"{w['win_prob_when_locked']*100:5.1f}% -> {w['win_prob_now']*100:5.1f}%")
    if cur["changes"]:
        print("\nchanges since the last slate:")
        for c in cur["changes"]:
            print(f"  [{c['type']}] {c['matchup']}: {c['text']}")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
