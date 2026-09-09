#!/usr/bin/env python3
"""Offline test of the context collector. No network.

Covers the parts that would fail silently in production: injury state
transitions across days, the position/status filter, weather parsing, and the
roofed-stadium short circuit.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import context as C  # noqa: E402

FAILS = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}" + ("" if ok else f" want {want!r}"))
    if not ok:
        FAILS.append(label)


def espn(entries):
    """Build an ESPN-shaped injuries payload. entries: [(team,name,pos,status)]"""
    by = {}
    for team, name, pos, status in entries:
        by.setdefault(team, []).append({
            "status": status,
            "details": {"type": "Knee"},
            "athlete": {"displayName": name, "position": {"displayName": pos}},
        })
    return {"injuries": [{"displayName": t, "injuries": v} for t, v in by.items()]}


def main():
    tmp = tempfile.mkdtemp()
    C.DATA = tmp
    C.INJURIES = os.path.join(tmp, "injuries.json")
    C.CONTEXT = os.path.join(tmp, "context.json")

    print("injury state transitions")
    C.get_json = lambda u, timeout=40: espn([
        ("Buffalo Bills", "Josh Allen", "Quarterback", "Questionable"),
        ("Buffalo Bills", "Some Guard", "Guard", "Questionable"),
        ("Miami Dolphins", "A Receiver", "Wide Receiver", "Out"),
    ])
    by, store = C.refresh_injuries("2026-09-10")
    json.dump(store, open(C.INJURIES, "w"))
    check("records created", len(store), 3)
    check("QB first_seen", store["Buffalo Bills|Josh Allen"]["first_seen"], "2026-09-10")

    buf = C.notable(by.get("Buffalo Bills"))
    check("QB surfaces at Questionable", [p["name"] for p in buf], ["Josh Allen"])
    check("non-QB Questionable filtered out", len(buf), 1)
    mia = C.notable(by.get("Miami Dolphins"))
    check("skill player surfaces when Out", [p["name"] for p in mia], ["A Receiver"])

    print("\nstatus worsens two days later")
    C.get_json = lambda u, timeout=40: espn([
        ("Buffalo Bills", "Josh Allen", "Quarterback", "Out"),
        ("Buffalo Bills", "Some Guard", "Guard", "Questionable"),
        ("Miami Dolphins", "A Receiver", "Wide Receiver", "Out"),
    ])
    by, store = C.refresh_injuries("2026-09-12")
    json.dump(store, open(C.INJURIES, "w"))
    a = store["Buffalo Bills|Josh Allen"]
    check("status updated", a["status"], "Out")
    check("first_seen preserved", a["first_seen"], "2026-09-10")
    check("change date moves — this is the pool signal", a["status_changed_on"], "2026-09-12")
    g = store["Buffalo Bills|Some Guard"]
    check("unchanged player keeps old change date", g["status_changed_on"], "2026-09-10")

    print("\nplayer drops off the report entirely")
    C.get_json = lambda u, timeout=40: espn([
        ("Buffalo Bills", "Some Guard", "Guard", "Questionable"),
    ])
    by, store = C.refresh_injuries("2026-09-13")
    check("cleared is recorded as a change", store["Buffalo Bills|Josh Allen"]["status"], "Cleared")
    check("cleared dated", store["Buffalo Bills|Josh Allen"]["status_changed_on"], "2026-09-13")
    check("cleared players do not show on the board",
          C.notable(by.get("Buffalo Bills")), [])

    print("\nweather")
    kick = datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc)
    check("roofed stadium short-circuits, no fetch",
          C.weather_for("Detroit Lions", kick), {"roofed": True})

    C.get_json = lambda u, timeout=25: {"hourly": {
        "time": ["2026-09-13T16:00", "2026-09-13T17:00", "2026-09-13T18:00"],
        "temperature_2m": [61.0, 63.5, 64.0],
        "precipitation_probability": [10, 20, 25],
        "wind_speed_10m": [9.0, 18.5, 12.0],
        "wind_gusts_10m": [14.0, 29.0, 20.0],
    }}
    w = C.weather_for("Buffalo Bills", kick)
    check("picks the kickoff hour, not the first hour", w["wind_mph"], 18.5)
    check("windy flag set above threshold", w["windy"], True)
    check("temp at kickoff", w["temp_f"], 63.5)
    check("gust carried", w["gust_mph"], 29.0)

    C.get_json = lambda u, timeout=25: {"hourly": {
        "time": ["2026-09-13T17:00"], "temperature_2m": [70.0],
        "precipitation_probability": [0], "wind_speed_10m": [6.0], "wind_gusts_10m": [9.0]}}
    check("calm game not flagged", C.weather_for("Green Bay Packers", kick)["windy"], False)

    print("\ndegradation")
    C.get_json = lambda u, timeout=25: None
    check("dead weather API returns None, does not raise",
          C.weather_for("Buffalo Bills", kick), None)
    C.get_json = lambda u, timeout=40: None
    by, store = C.refresh_injuries("2026-09-14")
    check("dead injury API returns empty, keeps store", by, {})

    shutil.rmtree(tmp)
    print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILURES: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
