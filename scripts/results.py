#!/usr/bin/env python3
"""
Fetch final scores for games we track and write data/results.json.

Runs in the collector (GitHub Actions), where there is real internet. Matches
ESPN's scoreboard to our games by team names and kickoff date -- the two sources
use different game ids but the same full team names.

Only completed games are recorded, and a result once written is never changed.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
GAMES = os.path.join(DATA, "games.json")
RESULTS = os.path.join(DATA, "results.json")

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
LOOKBACK_DAYS = 12   # enough to catch a game we missed for a week


def get_json(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": "nfl-line-tracker/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError, TimeoutError) as e:
        print(f"  warn: scoreboard unavailable ({e})", file=sys.stderr)
        return None


def load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError:
        return default


def game_day(iso):
    """US game day: shift back 8 hours so Monday night is Monday, not Tuesday."""
    k = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (k - timedelta(hours=8)).date()


def scrape(start, end):
    """-> {(away, home, gameday): {"away_score": int, "home_score": int}}"""
    url = f"{SCOREBOARD}?dates={start:%Y%m%d}-{end:%Y%m%d}&limit=400"
    data = get_json(url)
    out = {}
    if not data:
        return out
    for ev in data.get("events", []):
        for comp in ev.get("competitions", []):
            status = ((comp.get("status") or {}).get("type") or {})
            if not status.get("completed"):
                continue
            home = away = None
            hs = as_ = None
            for c in comp.get("competitors", []):
                name = ((c.get("team") or {}).get("displayName"))
                try:
                    score = int(c.get("score"))
                except (TypeError, ValueError):
                    score = None
                if c.get("homeAway") == "home":
                    home, hs = name, score
                else:
                    away, as_ = name, score
            if not (home and away) or hs is None or as_ is None:
                continue
            try:
                day = game_day(comp.get("date") or ev.get("date"))
            except (ValueError, AttributeError, TypeError):
                continue
            out[(away, home, day)] = {"away_score": as_, "home_score": hs}
    return out


def main():
    games = load(GAMES, {})
    if not games:
        sys.exit("No games.json — run collect.py first.")
    results = load(RESULTS, {})

    now = datetime.now(timezone.utc)
    found = scrape(now - timedelta(days=LOOKBACK_DAYS), now + timedelta(days=1))
    if not found:
        print("results: nothing completed in window (or source unavailable)")
        return

    added = 0
    for gid, g in games.items():
        if gid in results:
            continue                      # never rewrite a recorded result
        try:
            day = game_day(g["commence_time"])
        except (ValueError, KeyError):
            continue
        hit = found.get((g["away_team"], g["home_team"], day))
        if not hit:
            continue
        margin = hit["home_score"] - hit["away_score"]   # home minus away
        results[gid] = {
            "week": g.get("week"),
            "away_team": g["away_team"], "home_team": g["home_team"],
            "away_score": hit["away_score"], "home_score": hit["home_score"],
            "home_margin": margin,
            "closing_consensus": g["current"]["consensus"],
            "recorded_at": now.isoformat(timespec="seconds"),
        }
        added += 1

    with open(RESULTS, "w") as f:
        json.dump(results, f, indent=1, sort_keys=True)
    print(f"results: {added} newly completed, {len(results)} on file")


if __name__ == "__main__":
    main()
