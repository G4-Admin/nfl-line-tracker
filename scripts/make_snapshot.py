#!/usr/bin/env python3
"""Emit the newest collected snapshot for the board's "Check for newer lines" button.

Reconstructed locally on 2026-09-08 because scripts/make_snapshot.py has never
been committed to the collector repo (404 on raw). The shape is taken from the
published page's applySnapshot():

    { "date": "YYYY-MM-DD",
      "lines":   { "<game_id>": { "consensus": .., "n_books": .., "min": .., ... } },
      "results": { "<game_id>": { "home_score": .., "away_score": .., "margin": .. } } }

Only rows whose current snapshot carries the newest date are included in
`lines` -- a game the collector did not price today keeps whatever the board
already has.

`results` was added 2026-09-15. The refresh button reaches the datastore and
nothing else: it cannot read games.json, so before this the button updated the
market and nothing else, and a board left open across a Sunday showed fresh
lines on games that had already been played. Results are NOT date-filtered --
every known final is sent, because a board opened after a week away needs all
of them, and a result never changes once recorded.

Committed to the repo 2026-10-13. Until then this file lived only as the project
doc claude/scripts/make_snapshot.py.
"""
import argparse
import json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.games) as f:
        games = json.load(f)

    dates = [g["current"]["date"] for g in games.values()
             if g.get("current") and g["current"].get("date")]
    if not dates:
        raise SystemExit("no dated snapshots in games file")
    date = max(dates)

    lines = {}
    for gid, g in games.items():
        cur = g.get("current") or {}
        if cur.get("date") != date or cur.get("consensus") is None:
            continue
        lines[gid] = {k: v for k, v in cur.items()
                      if k not in ("date", "at") and v is not None}

    results = {}
    for gid, g in games.items():
        r = g.get("result") or {}
        if r.get("margin") is None:
            continue
        results[gid] = {"home_score": r.get("home_score"),
                        "away_score": r.get("away_score"),
                        "margin": r.get("margin"),
                        "recorded_at": r.get("recorded_at")}

    snap = {"date": date, "games": len(games), "generated_at": None,
            "lines": lines, "results": results}
    with open(args.out, "w") as f:
        json.dump(snap, f, separators=(",", ":"), sort_keys=True)

    kb = len(json.dumps(snap)) / 1024
    print(f"wrote {args.out}  date={date}  lines={len(lines)}/{len(games)}  "
          f"results={len(results)}  size={kb:.0f}KB")


if __name__ == "__main__":
    main()
