#!/usr/bin/env python3
"""Sign and sanity check on a transcribed pool sheet.

Reconstructed locally 2026-09-09; scripts/validate_sheet.py has never been
committed (404 on raw). The rules are taken verbatim from
claude/saturday-picks-runbook.md, "Transcription is automatic":

  Pool numbers and market numbers are two measurements of the same games two
  days apart, so they must correlate strongly and positively. A sign error
  flips the correlation to about -0.9, which nothing else does.

  - negative or weak correlation, or a median pool-vs-market gap over 4 points
    -> the sheet is REJECTED, no picks are built
  - a single line more than 14 points off the market is a typo: warn, exclude
    it from the checks and from picks, continue the week
  - any game that cannot be matched appears in `unmatched`; check it every time

Committed to the repo 2026-10-13 as part of the engine migration.
"""
import argparse
import json
import statistics
import sys

OUTLIER = 14.0
MAX_MEDIAN_GAP = 4.0
MIN_CORR = 0.5


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def match(sheet, games):
    """-> (matched rows, unmatched sheet rows)"""
    by_pair = {(g["home_team"], g["away_team"]): g for g in games.values()}
    rows, unmatched = [], []
    for ln in sheet["lines"]:
        g = by_pair.get((ln["home"], ln["away"]))
        if not g:
            unmatched.append(ln)
            continue
        cur = g.get("current") or {}
        rows.append({
            "game_id": g["game_id"],
            "home_team": g["home_team"],
            "away_team": g["away_team"],
            "commence_time": g["commence_time"],
            "pool_line": float(ln["home_spread"]),
            "market_line": cur.get("consensus"),
            "n_books": cur.get("n_books"),
            "week": g["week"],
        })
    return rows, unmatched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--games", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()

    sheet = json.load(open(args.pool))
    games = json.load(open(args.games))
    rows, unmatched = match(sheet, games)

    priced = [r for r in rows if r["market_line"] is not None]
    for r in priced:
        r["gap"] = round(r["pool_line"] - r["market_line"], 1)

    outliers = [r for r in priced if abs(r["gap"]) > OUTLIER]
    kept = [r for r in priced if abs(r["gap"]) <= OUTLIER]

    corr = pearson([r["pool_line"] for r in kept], [r["market_line"] for r in kept])
    median_gap = statistics.median([abs(r["gap"]) for r in kept]) if kept else None

    verdict, reasons = "ACCEPTED", []
    if corr is None:
        verdict, reasons = "REJECTED", ["correlation undefined (too few lines, or no variation)"]
    else:
        if corr < MIN_CORR:
            verdict = "REJECTED"
            reasons.append(f"pool/market correlation {corr:+.3f} is not strongly positive "
                           f"(< {MIN_CORR}); a sign error looks like this")
        if median_gap is not None and median_gap > MAX_MEDIAN_GAP:
            verdict = "REJECTED"
            reasons.append(f"median pool-vs-market gap {median_gap:.1f} exceeds {MAX_MEDIAN_GAP}")

    print(f"matched {len(rows)}/{len(sheet['lines'])} sheet lines; "
          f"{len(priced)} priced by the market")
    print(f"correlation pool vs market: {corr:+.4f}" if corr is not None else "correlation: n/a")
    print(f"median |gap|: {median_gap}")
    if outliers:
        print("OUTLIERS (>14 pts, excluded from checks and from picks):")
        for r in outliers:
            print(f"  ! {r['away_team']} @ {r['home_team']}  pool {r['pool_line']:+.1f} "
                  f"vs market {r['market_line']:+.1f}  gap {r['gap']:+.1f}")
    if unmatched:
        print("UNMATCHED sheet lines — check these every time:")
        for ln in unmatched:
            print(f"  ? {ln['away']} @ {ln['home']}")
    print(f"VERDICT: {verdict}")
    for r in reasons:
        print("  -", r)

    if args.out:
        json.dump({"verdict": verdict, "reasons": reasons, "correlation": corr,
                   "median_gap": median_gap, "rows": kept, "outliers": outliers,
                   "unmatched": unmatched}, open(args.out, "w"), indent=1)
        print(f"wrote {args.out}")

    if verdict == "REJECTED":
        sys.exit(2)


if __name__ == "__main__":
    main()
