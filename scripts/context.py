#!/usr/bin/env python3
"""
Game context: the things that can still move a final score AFTER the pool's line
is frozen Thursday morning.

Deliberately narrow. The pool's lines track the market on the day they are sent,
which means every season-long team stat -- ATS record, points per game, point
differential, home/road splits -- is already inside that number before it reaches
you. Recomputing them cannot produce an edge. What is NOT yet in the number is
news that breaks after it is set. That is all this collects:

  1. Injury status changes, weighted by position. A quarterback change is worth
     several points; nothing else on a roster comes close.
  2. Weather at outdoor stadiums near kickoff, wind above all.

Explicitly NOT collected, and why:

  * Rest / bye / short week. Peer-reviewed work (Frontiers in Behavioral
    Economics, 2024) finds the bye-week advantage was worth about +2.2 points
    per game before 2011 and shows no significant effect since. The short-week
    angle does not survive testing either. Building it would be building a
    narrative.
  * ATS records and season cover totals. Priced, and not predictive of future
    cover results.
  * Team offensive/defensive rankings. Priced.

Writes data/context.json and maintains data/injuries.json, which carries a
first_seen and status_changed_on date per player so "what changed since the pool
sheet went out" is answerable from one file.

Every fetch degrades gracefully: a source that is down costs you that section,
not the run.
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
CONTEXT = os.path.join(DATA, "context.json")
INJURIES = os.path.join(DATA, "injuries.json")

ESPN_INJURIES = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"

HORIZON_DAYS = 10          # only look at games this close to kickoff
WIND_FLAG_MPH = 15         # below this, wind does not meaningfully change a game

# Positions that actually move a final score when they are missing, in order.
# A backup quarterback is a different team; a backup guard usually is not.
POSITION_TIER = {
    "Quarterback": 1,
    "Left Tackle": 2, "Offensive Tackle": 2, "Center": 2, "Guard": 2,
    "Offensive Lineman": 2,
    "Wide Receiver": 3, "Running Back": 3, "Tight End": 3,
    "Cornerback": 3, "Defensive End": 3, "Edge": 3, "Linebacker": 3,
    "Safety": 4, "Defensive Tackle": 4,
}
SERIOUS = {"Out", "Doubtful", "Injured Reserve"}

# lat, lon, roofed. Retractable roofs are treated as roofed: they are shut when
# the weather is bad, which is exactly when we would otherwise flag the game.
STADIUMS = {
    "Arizona Cardinals": (33.5276, -112.2626, True),
    "Atlanta Falcons": (33.7554, -84.4009, True),
    "Baltimore Ravens": (39.2780, -76.6227, False),
    "Buffalo Bills": (42.7738, -78.7870, False),
    "Carolina Panthers": (35.2258, -80.8528, False),
    "Chicago Bears": (41.8623, -87.6167, False),
    "Cincinnati Bengals": (39.0955, -84.5161, False),
    "Cleveland Browns": (41.5061, -81.6995, False),
    "Dallas Cowboys": (32.7473, -97.0945, True),
    "Denver Broncos": (39.7439, -105.0201, False),
    "Detroit Lions": (42.3400, -83.0456, True),
    "Green Bay Packers": (44.5013, -88.0622, False),
    "Houston Texans": (29.6847, -95.4107, True),
    "Indianapolis Colts": (39.7601, -86.1639, True),
    "Jacksonville Jaguars": (30.3239, -81.6373, False),
    "Kansas City Chiefs": (39.0489, -94.4839, False),
    "Las Vegas Raiders": (36.0909, -115.1833, True),
    "Los Angeles Chargers": (33.9535, -118.3392, True),
    "Los Angeles Rams": (33.9535, -118.3392, True),
    "Miami Dolphins": (25.9580, -80.2389, False),
    "Minnesota Vikings": (44.9738, -93.2578, True),
    "New England Patriots": (42.0909, -71.2643, False),
    "New Orleans Saints": (29.9511, -90.0812, True),
    "New York Giants": (40.8135, -74.0745, False),
    "New York Jets": (40.8135, -74.0745, False),
    "Philadelphia Eagles": (39.9008, -75.1675, False),
    "Pittsburgh Steelers": (40.4468, -80.0158, False),
    "San Francisco 49ers": (37.4033, -121.9694, False),
    "Seattle Seahawks": (47.5952, -122.3316, False),
    "Tampa Bay Buccaneers": (27.9759, -82.5033, False),
    "Tennessee Titans": (36.1665, -86.7713, False),
    "Washington Commanders": (38.9077, -76.8645, False),
}


def get_json(url, timeout=40):
    req = urllib.request.Request(url, headers={
        # ESPN answers 403 to a custom agent from a datacenter IP. Measured
        # 2026-09-09: both its injuries and scoreboard endpoints refused the
        # GitHub runner with "nfl-line-tracker/1.0", while Open-Meteo served
        # ten forecasts on the same run through this same function -- so it is
        # ESPN refusing the agent, not the network.
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/128.0.0.0 Safari/537.36"),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError, TimeoutError) as e:
        print(f"  warn: {url.split('?')[0]} unavailable ({e})", file=sys.stderr)
        return None


def load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError:
        return default


# ---------------------------------------------------------------------------
# Injuries
# ---------------------------------------------------------------------------

def refresh_injuries(today):
    """Update the persistent injury record. Returns {team: [entry, ...]}.

    Each entry keeps first_seen and status_changed_on so a later run can answer
    "what is different since the pool sheet went out" without a second source.
    """
    store = load(INJURIES, {})
    payload = get_json(ESPN_INJURIES)
    if not payload:
        return {}, store

    seen = set()
    for team_block in payload.get("injuries", []):
        team = team_block.get("displayName")
        if not team:
            continue
        for item in team_block.get("injuries", []):
            ath = item.get("athlete") or {}
            name = ath.get("displayName")
            if not name:
                continue
            pos = ((ath.get("position") or {}).get("displayName")) or "Unknown"
            status = item.get("status") or "Unknown"
            detail = (item.get("details") or {}).get("type") or ""
            key = f"{team}|{name}"
            seen.add(key)

            prev = store.get(key)
            if prev is None:
                store[key] = {
                    "team": team, "name": name, "position": pos, "status": status,
                    "detail": detail, "first_seen": today,
                    "status_changed_on": today, "last_seen": today,
                }
            else:
                if prev.get("status") != status:
                    prev["status"] = status
                    prev["status_changed_on"] = today
                prev["position"] = pos
                prev["detail"] = detail or prev.get("detail", "")
                prev["last_seen"] = today

    # Anyone who dropped off the report is healthy again; that is a change too.
    for key, e in store.items():
        if key not in seen and e.get("status") != "Cleared":
            e["status"] = "Cleared"
            e["status_changed_on"] = today
            e["last_seen"] = today

    by_team = {}
    for e in store.values():
        by_team.setdefault(e["team"], []).append(e)
    return by_team, store


def notable(entries):
    """Filter a team's injury list down to what can actually move a score."""
    out = []
    for e in entries or []:
        if e.get("status") == "Cleared":
            continue
        tier = POSITION_TIER.get(e.get("position", ""), 5)
        status = e.get("status", "")
        # Quarterbacks matter at any designation. Everyone else has to be
        # seriously in doubt before it is worth a line on the board.
        if tier == 1 or (tier <= 3 and status in SERIOUS):
            out.append({
                "name": e["name"], "position": e["position"], "status": status,
                "detail": e.get("detail", ""), "tier": tier,
                "changed_on": e.get("status_changed_on"),
            })
    out.sort(key=lambda x: (x["tier"], x["name"]))
    return out[:6]


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------

def weather_for(home_team, kickoff_utc):
    spot = STADIUMS.get(home_team)
    if not spot:
        return None
    lat, lon, roofed = spot
    if roofed:
        return {"roofed": True}

    day = kickoff_utc.date().isoformat()
    url = (f"{OPEN_METEO}?latitude={lat}&longitude={lon}"
           f"&hourly=temperature_2m,precipitation_probability,wind_speed_10m,wind_gusts_10m"
           f"&wind_speed_unit=mph&temperature_unit=fahrenheit&timezone=UTC"
           f"&start_date={day}&end_date={day}")
    data = get_json(url, timeout=25)
    if not data or "hourly" not in data:
        return None

    h = data["hourly"]
    target = kickoff_utc.strftime("%Y-%m-%dT%H:00")
    try:
        i = h["time"].index(target)
    except (ValueError, KeyError):
        return None

    def at(field):
        try:
            return h[field][i]
        except (KeyError, IndexError, TypeError):
            return None

    wind = at("wind_speed_10m")
    gust = at("wind_gusts_10m")
    return {
        "roofed": False,
        "temp_f": at("temperature_2m"),
        "precip_pct": at("precipitation_probability"),
        "wind_mph": wind,
        "gust_mph": gust,
        "windy": bool(wind is not None and wind >= WIND_FLAG_MPH),
    }


# ---------------------------------------------------------------------------

def main():
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    horizon = now + timedelta(days=HORIZON_DAYS)

    games = load(GAMES, {})
    if not games:
        sys.exit("No games.json — run collect.py first.")

    inj_by_team, store = refresh_injuries(today)

    out = {}
    weather_calls = 0
    for gid, g in games.items():
        kick = datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00"))
        if kick <= now or kick > horizon:
            continue

        entry = {
            "matchup": f"{g['away_team']} @ {g['home_team']}",
            "kickoff": g["commence_time"],
            "injuries": {
                "home": notable(inj_by_team.get(g["home_team"])),
                "away": notable(inj_by_team.get(g["away_team"])),
            },
        }
        w = weather_for(g["home_team"], kick)
        if w:
            entry["weather"] = w
            if not w.get("roofed"):
                weather_calls += 1
        out[gid] = entry

    with open(INJURIES, "w") as f:
        json.dump(store, f, indent=1, sort_keys=True)
    with open(CONTEXT, "w") as f:
        json.dump({"generated_at": now.isoformat(timespec="seconds"),
                   "horizon_days": HORIZON_DAYS,
                   "wind_flag_mph": WIND_FLAG_MPH,
                   "games": out}, f, indent=1, sort_keys=True)

    qbs = sum(1 for e in out.values()
              for side in e["injuries"].values()
              for p in side if p["tier"] == 1)
    windy = sum(1 for e in out.values() if e.get("weather", {}).get("windy"))
    print(f"context: {len(out)} games in the next {HORIZON_DAYS}d  "
          f"quarterbacks_on_report={qbs}  windy_venues={windy}  "
          f"forecasts_fetched={weather_calls}  injury_records={len(store)}")


if __name__ == "__main__":
    main()
