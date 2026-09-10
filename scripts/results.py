#!/usr/bin/env python3
"""
Fetch final scores and attach them to the games we track.

Source: The Odds API's scores endpoint -- the SAME API, key and host that
collect.py already uses successfully on every run.

Why not ESPN, which this script used until 2026-09-10: ESPN answers
403 Forbidden to GitHub Actions runners. Measured 2026-09-09 across three
dispatches, including one sending a full browser User-Agent, while Open-Meteo
served ten forecasts on the same runs. It is an IP-level block, not an agent
problem, and there is no version of a User-Agent that fixes it. Do not try.

Matching is EXACT and needs no fuzzing: collect.py sets each game's id from
`ev["id"]` on the odds endpoint, and the scores endpoint returns that same id
for the same event.

Two files are written, deliberately:

  data/results.json  the durable record. A result, once written, is never
                     changed -- a score that can be rewritten is not a record.
  data/games.json    each completed game gains a `result` block, which is what
                     the board grades from. collect.py rewrites games.json on
                     every run and does not preserve it, so EVERY known result
                     is re-applied here each time rather than only new ones.
                     That makes this step self-healing instead of order-dependent.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
GAMES = os.path.join(DATA, "games.json")
RESULTS = os.path.join(DATA, "results.json")

API_BASE = "https://api.the-odds-api.com/v4"
SPORT = "americanfootball_nfl"

# The endpoint's maximum. Costs 2 credits with daysFrom set (1 without), against
# a 500/month allowance currently spending ~1/day. Anything finishing more than
# three days ago can never be recovered from this endpoint, which is fine while
# the collector runs daily -- it would take four consecutive failed runs to lose
# a game, and results.json keeps everything already seen.
DAYS_FROM = 3


def get_json(url, timeout=40):
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/128.0.0.0 Safari/537.36"),
        "Accept": "application/json, text/plain, */*",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError, TimeoutError) as e:
        print(f"  warn: scores unavailable ({e})", file=sys.stderr)
        return None


def load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError:
        return default


def fetch_scores(api_key):
    """-> {event_id: {"home_score": int, "away_score": int}} for completed games."""
    url = (f"{API_BASE}/sports/{SPORT}/scores/"
           f"?apiKey={api_key}&daysFrom={DAYS_FROM}&dateFormat=iso")
    data = get_json(url)
    if data is None:
        return None                      # distinguish "failed" from "nothing final"
    out, skipped = {}, 0
    for ev in data or []:
        if not ev.get("completed"):
            continue                     # in progress, or not started
        scores = ev.get("scores") or []
        by_name = {}
        for s in scores:
            try:
                by_name[s.get("name")] = int(s.get("score"))
            except (TypeError, ValueError):
                pass                     # the API returns score as a STRING
        home, away = ev.get("home_team"), ev.get("away_team")
        if home not in by_name or away not in by_name:
            skipped += 1                 # completed but unscored, or renamed team
            continue
        out[ev.get("id")] = {"home_score": by_name[home], "away_score": by_name[away]}
    if skipped:
        print(f"  note: {skipped} completed event(s) had no usable score line")
    return out


def main():
    api_key = os.environ.get("ODDS_API_KEY", "").strip()
    if not api_key:
        sys.exit("ODDS_API_KEY is not set.")

    games = load(GAMES, {})
    if not games:
        sys.exit("No games.json — run collect.py first.")
    results = load(RESULTS, {})
    before = len(results)

    found = fetch_scores(api_key)
    if found is None:
        # The fetch failed. Still re-apply what is already on file so a bad day
        # upstream does not silently strip results off the board.
        print("results: source unavailable; re-applying known results only")
        found = {}

    now = datetime.now(timezone.utc)
    for gid, sc in found.items():
        if gid in results:
            continue                     # never rewrite a recorded result
        g = games.get(gid)
        if not g:
            continue                     # a game we do not track
        results[gid] = {
            "week": g.get("week"),
            "away_team": g["away_team"], "home_team": g["home_team"],
            "away_score": sc["away_score"], "home_score": sc["home_score"],
            "home_margin": sc["home_score"] - sc["away_score"],
            "closing_consensus": (g.get("current") or {}).get("consensus"),
            "recorded_at": now.isoformat(timespec="seconds"),
        }

    # Re-apply every known result onto games.json. `margin` is HOME minus AWAY,
    # matching the sign convention every spread in this system uses.
    applied = 0
    for gid, r in results.items():
        g = games.get(gid)
        if not g:
            continue
        g["result"] = {
            "home_score": r["home_score"],
            "away_score": r["away_score"],
            "margin": r["home_margin"],
            "recorded_at": r.get("recorded_at"),
        }
        applied += 1

    with open(RESULTS, "w") as f:
        json.dump(results, f, indent=1, sort_keys=True)
    with open(GAMES, "w") as f:
        json.dump(games, f, indent=1, sort_keys=True)

    print(f"results: {len(results) - before} newly final, {len(results)} on file, "
          f"{applied} applied to games.json")


if __name__ == "__main__":
    main()
