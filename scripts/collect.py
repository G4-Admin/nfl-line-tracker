#!/usr/bin/env python3
"""
NFL line tracker - collector.

Pulls current NFL point spreads from The Odds API and maintains three files:

  data/snapshots.csv  full fidelity: one row per (run, game, book)
  data/games.json     per-game state: opening line, consensus history, current line
  data/movement.json  small digest of what moved since the last run (feeds the analyst)

Design rules:
  * The FIRST consensus line we ever see for a game is its opener. Never overwritten.
  * Re-running on the same day overwrites that day's consensus point (last run wins)
    but always appends to snapshots.csv, so nothing is ever destroyed.
  * Games that have already kicked off stop updating; their last pre-kickoff
    consensus is frozen as the closing line.

Env:
  ODDS_API_KEY   required
  SEASON_ANCHOR  optional, ISO date of the Tuesday before Week 1 (default 2026-09-08)
"""

import csv
import json
import os
import statistics
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone, date

API_BASE = "https://api.the-odds-api.com/v4"
SPORT = "americanfootball_nfl"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

SNAPSHOTS = os.path.join(DATA, "snapshots.csv")
GAMES = os.path.join(DATA, "games.json")
MOVEMENT = os.path.join(DATA, "movement.json")

# Books we surface individually on the dashboard; everything else still feeds consensus.
FEATURED = ["draftkings", "fanduel", "betmgm", "williamhill_us"]

SNAPSHOT_FIELDS = [
    "fetched_at", "game_id", "commence_time", "week",
    "away_team", "home_team", "book", "home_spread", "home_price", "away_price",
]


def fetch_odds(api_key):
    url = (
        f"{API_BASE}/sports/{SPORT}/odds/"
        f"?apiKey={api_key}&regions=us&markets=spreads"
        f"&oddsFormat=american&dateFormat=iso"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "nfl-line-tracker/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            quota = {
                "remaining": resp.headers.get("x-requests-remaining"),
                "used": resp.headers.get("x-requests-used"),
                "cost": resp.headers.get("x-requests-last"),
            }
            return body, quota
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        sys.exit(f"Odds API returned HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach the Odds API: {e.reason}")


def nfl_week(commence_iso, anchor):
    """Week 1 = the 7 days starting the Tuesday before the first game."""
    try:
        game_day = datetime.fromisoformat(commence_iso.replace("Z", "+00:00")).date()
    except ValueError:
        return None
    delta = (game_day - anchor).days
    if delta < 0:
        return None
    return delta // 7 + 1


def extract_book_lines(event):
    """-> {book_key: {"home_spread": float, "home_price": int, "away_price": int}}"""
    out = {}
    home, away = event.get("home_team"), event.get("away_team")
    for bk in event.get("bookmakers", []):
        for market in bk.get("markets", []):
            if market.get("key") != "spreads":
                continue
            hs = hp = ap = None
            for oc in market.get("outcomes", []):
                if oc.get("name") == home:
                    hs, hp = oc.get("point"), oc.get("price")
                elif oc.get("name") == away:
                    ap = oc.get("price")
            if hs is not None:
                out[bk["key"]] = {
                    "home_spread": float(hs),
                    "home_price": hp,
                    "away_price": ap,
                }
    return out


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return default


def append_snapshots(rows):
    os.makedirs(DATA, exist_ok=True)
    new_file = not os.path.exists(SNAPSHOTS)
    with open(SNAPSHOTS, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SNAPSHOT_FIELDS)
        if new_file:
            w.writeheader()
        w.writerows(rows)


def main():
    api_key = os.environ.get("ODDS_API_KEY", "").strip()
    if not api_key:
        sys.exit("ODDS_API_KEY is not set.")

    anchor = date.fromisoformat(os.environ.get("SEASON_ANCHOR", "2026-09-08"))
    now = datetime.now(timezone.utc)
    run_at = now.isoformat(timespec="seconds")
    today = now.date().isoformat()

    events, quota = fetch_odds(api_key)
    games = load_json(GAMES, {})
    # On the very first run every game is already on the board, so what we capture
    # is a baseline, not a true opener. After that, a game we've never seen before
    # is one whose line just posted -- that IS the opener.
    first_run = not games
    open_source = "baseline_at_setup" if first_run else "first_capture"

    snapshot_rows = []
    movers = []
    seen_now = 0

    for ev in events:
        gid = ev.get("id")
        commence = ev.get("commence_time")
        home, away = ev.get("home_team"), ev.get("away_team")
        if not (gid and commence and home and away):
            continue

        kickoff = datetime.fromisoformat(commence.replace("Z", "+00:00"))
        if kickoff <= now:
            continue  # already started; its last snapshot stands as the close

        books = extract_book_lines(ev)
        if not books:
            continue

        seen_now += 1
        week = nfl_week(commence, anchor)
        spreads = [b["home_spread"] for b in books.values()]
        consensus = round(statistics.median(spreads) * 2) / 2

        for bk, vals in books.items():
            snapshot_rows.append({
                "fetched_at": run_at, "game_id": gid, "commence_time": commence,
                "week": week, "away_team": away, "home_team": home, "book": bk,
                "home_spread": vals["home_spread"],
                "home_price": vals["home_price"], "away_price": vals["away_price"],
            })

        point = {
            "date": today,
            "at": run_at,
            "consensus": consensus,
            "n_books": len(books),
            "min": min(spreads),
            "max": max(spreads),
        }
        for b in FEATURED:
            point[b] = books[b]["home_spread"] if b in books else None

        g = games.get(gid)
        if g is None:
            # First time we have ever seen a line for this game: this is the opener.
            games[gid] = {
                "game_id": gid,
                "week": week,
                "commence_time": commence,
                "home_team": home,
                "away_team": away,
                "opened": {"date": today, "at": run_at, "consensus": consensus,
                           "n_books": len(books), "source": open_source},
                "history": [point],
                "current": point,
                "notes": [],
            }
            movers.append({
                "game_id": gid, "week": week, "matchup": f"{away} @ {home}",
                "commence_time": commence,
                "event": "baseline" if first_run else "new_line",
                "open": consensus, "prev": None, "current": consensus, "delta": 0.0,
            })
            continue

        g["week"] = week
        g["commence_time"] = commence
        prev = g["current"]["consensus"] if g.get("current") else consensus

        # One consensus point per calendar day; a same-day re-run replaces it.
        if g["history"] and g["history"][-1]["date"] == today:
            prev = (g["history"][-2]["consensus"] if len(g["history"]) > 1 else
                    g["opened"]["consensus"])
            g["history"][-1] = point
        else:
            g["history"].append(point)
        g["current"] = point

        delta_day = round(consensus - prev, 1)
        delta_open = round(consensus - g["opened"]["consensus"], 1)
        crossed = key_number_crossed(prev, consensus)

        if abs(delta_day) >= 0.5 or crossed:
            movers.append({
                "game_id": gid, "week": week, "matchup": f"{away} @ {home}",
                "commence_time": commence,
                "event": "key_number" if crossed else "move",
                "open": g["opened"]["consensus"], "prev": prev, "current": consensus,
                "delta": delta_day, "delta_from_open": delta_open,
                "crossed": crossed,
                "book_spread": round(max(spreads) - min(spreads), 1),
            })

    append_snapshots(snapshot_rows)

    with open(GAMES, "w") as f:
        json.dump(games, f, indent=1, sort_keys=True)

    movers.sort(key=lambda m: (-abs(m.get("delta") or 0), m["commence_time"]))
    with open(MOVEMENT, "w") as f:
        json.dump({
            "generated_at": run_at,
            "date": today,
            "games_tracked": seen_now,
            "quota": quota,
            "movers": movers,
        }, f, indent=1)

    print(f"{run_at}  games={seen_now}  rows={len(snapshot_rows)}  "
          f"movers={len(movers)}  quota_remaining={quota.get('remaining')}")


def key_number_crossed(prev, cur):
    """Did the line cross or land on 3 or 7 (either side)? Those are the numbers
    that actually change ticket value in the NFL."""
    if prev is None or prev == cur:
        return None
    lo, hi = sorted((abs(prev), abs(cur)))
    for k in (3, 7):
        if lo < k <= hi or hi < k <= lo:
            return k
        if abs(cur) == k and abs(prev) != k:
            return k
    return None


if __name__ == "__main__":
    main()
