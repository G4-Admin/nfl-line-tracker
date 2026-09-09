#!/usr/bin/env python3
"""
Head-to-head scorecard: Austin's actual submitted picks against the engine's.

  python scripts/ledger.py --ledger ledger.json [--out data/scorecard.json]

Grades every recorded pick against the POOL's line -- the Commish's number, which
is what the contest settles on -- and the final score.

WHAT THIS CAN AND CANNOT TELL YOU
---------------------------------
It can catch a systematic problem fast: a sign error in transcription, an edge
that never shows up at all, a Monday-night habit that keeps costing weeks. Those
appear within a handful of weeks and are worth catching.

It cannot, in one season, tell you whether the engine picks better than you do.
Five picks a week over eighteen weeks is ninety picks each. Separating a 55%
picker from a 50% one at conventional confidence needs roughly eight hundred --
the arithmetic is in `sample_size_note` and it is not close. Anyone who tells you
their 90-pick record proves something is reading noise.

The one comparison that carries real information is the DISAGREEMENTS. Weeks where
you both took the same side tell you nothing about who is better; they cancel. So
this report foregrounds the games where you went a different direction, and counts
those separately. They accumulate slowly -- maybe twenty or thirty a season -- so
that number will be even noisier in percentage terms. Read it as a running tally
worth watching over years, not a verdict on the season.

The honest use of this file is calibration, not competition: does a pick with two
points of price actually win more often than one with half a point? That question
pools every pick from both of you, so it accumulates twice as fast and has an
objectively right answer.
"""

import argparse
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Buckets for the calibration check, in points of price.
VALUE_BUCKETS = [(0.0, 0.01, "no edge"), (0.01, 1.0, "0.5 pt"),
                 (1.0, 2.0, "1–1.5 pts"), (2.0, 99.0, "2+ pts")]


def grade_pick(pick, result):
    """-> 'win' | 'loss' | 'push' | None (not yet played).

    A push counts as a loss in this pool, but it is recorded distinctly so the
    cost of landing on a key number stays visible.
    """
    if not result:
        return None
    margin = result["home_margin"]          # home minus away
    edge = margin + float(pick["pool_line"])
    if abs(edge) < 1e-9:
        return "push"
    covered_home = edge > 0
    return "win" if covered_home == (pick["side"] == "home") else "loss"


def tally(picks, results):
    """Record for one entrant across a set of picks."""
    t = {"win": 0, "loss": 0, "push": 0, "pending": 0,
         "mnf_win": 0, "mnf_loss": 0, "mnf_push": 0}
    for p in picks:
        g = grade_pick(p, results.get(p["game_id"]))
        if g is None:
            t["pending"] += 1
            continue
        t[g] += 1
        if p.get("is_mnf"):
            t[f"mnf_{g}"] += 1
    graded = t["win"] + t["loss"] + t["push"]
    t["graded"] = graded
    # A push loses, so it belongs in the denominator and not the numerator.
    t["win_pct"] = round(t["win"] / graded * 100, 1) if graded else None
    mnf_graded = t["mnf_win"] + t["mnf_loss"] + t["mnf_push"]
    t["mnf_graded"] = mnf_graded
    t["mnf_pct"] = round(t["mnf_win"] / mnf_graded * 100, 1) if mnf_graded else None
    return t


def week_perfect(picks, results):
    """Did this slate go 5-0? None if any pick is still pending."""
    outcomes = [grade_pick(p, results.get(p["game_id"])) for p in picks]
    if any(o is None for o in outcomes):
        return None
    return all(o == "win" for o in outcomes)


def sample_size_for(delta=0.05, base=0.50, alpha=0.05, power=0.80):
    """Picks needed to distinguish `base + delta` from `base`, two-sided."""
    z_a, z_b = 1.959964, 0.841621
    return math.ceil((z_a + z_b) ** 2 * base * (1 - base) / (delta ** 2))


def wilson(wins, n, z=1.959964):
    """Wilson interval — honest at small n, where the naive interval is not."""
    if not n:
        return None
    p = wins / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, centre - half) * 100, 1),
            round(min(1.0, centre + half) * 100, 1)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--results", default=os.path.join(ROOT, "data", "results.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "scorecard.json"))
    args = ap.parse_args()

    with open(args.ledger) as f:
        ledger = json.load(f)
    results = {}
    if os.path.exists(args.results):
        with open(args.results) as f:
            results = json.load(f)

    all_engine, all_austin = [], []
    weeks_out = []
    agree = disagree = 0
    dis_engine = {"win": 0, "loss": 0, "push": 0}
    dis_austin = {"win": 0, "loss": 0, "push": 0}
    calib = defaultdict(lambda: {"n": 0, "wins": 0, "predicted": 0.0})

    for wk in sorted(ledger.get("weeks", {}), key=lambda x: int(x)):
        w = ledger["weeks"][wk]
        eng, aus = w.get("engine", []), w.get("austin", [])
        all_engine += eng
        all_austin += aus

        by_game = {p["game_id"]: p for p in aus}
        for e in eng:
            a = by_game.get(e["game_id"])
            if a and a["side"] == e["side"]:
                agree += 1
            elif a:
                # Same game, opposite sides — the only true head-to-head.
                disagree += 1
                ge, ga = grade_pick(e, results.get(e["game_id"])), \
                         grade_pick(a, results.get(a["game_id"]))
                if ge:
                    dis_engine[ge] += 1
                if ga:
                    dis_austin[ga] += 1

        for p in eng + aus:
            g = grade_pick(p, results.get(p["game_id"]))
            if g is None:
                continue
            v = abs(float(p.get("line_value") or 0))
            for lo, hi, label in VALUE_BUCKETS:
                if lo <= v < hi:
                    calib[label]["n"] += 1
                    calib[label]["wins"] += 1 if g == "win" else 0
                    calib[label]["predicted"] += float(p.get("win_prob") or 0.5)
                    break

        weeks_out.append({
            "week": int(wk),
            "engine": tally(eng, results),
            "austin": tally(aus, results),
            "engine_perfect": week_perfect(eng, results) if eng else None,
            "austin_perfect": week_perfect(aus, results) if aus else None,
            "austin_source": w.get("austin_source"),
            "recorded": bool(aus),
        })

    eng_t, aus_t = tally(all_engine, results), tally(all_austin, results)
    need = sample_size_for()

    for label, c in calib.items():
        c["actual_pct"] = round(c["wins"] / c["n"] * 100, 1) if c["n"] else None
        c["predicted_pct"] = round(c["predicted"] / c["n"] * 100, 1) if c["n"] else None
        c["interval"] = wilson(c["wins"], c["n"])

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "weeks": weeks_out,
        "season": {
            "engine": eng_t, "austin": aus_t,
            "engine_interval": wilson(eng_t["win"], eng_t["graded"]),
            "austin_interval": wilson(aus_t["win"], aus_t["graded"]),
            "engine_perfect_weeks": sum(1 for w in weeks_out if w["engine_perfect"]),
            "austin_perfect_weeks": sum(1 for w in weeks_out if w["austin_perfect"]),
            "weeks_missing_your_picks": [w["week"] for w in weeks_out
                                         if not w["recorded"]],
        },
        "head_to_head": {
            "agreed": agree, "disagreed": disagree,
            "engine_on_disagreements": dis_engine,
            "austin_on_disagreements": dis_austin,
        },
        "calibration": dict(calib),
        "sample_size_note": {
            "picks_graded_each": eng_t["graded"],
            "picks_needed_to_separate_55_from_50": need,
            "text": f"Separating a 55% picker from a 50% one at 95% confidence with "
                    f"80% power needs about {need} picks. A full season is 90. Whatever "
                    f"the records below say, they do not settle who picks better — the "
                    f"intervals show how wide the uncertainty really is. Use this to "
                    f"catch something broken, not to crown someone.",
        },
    }

    with open(args.out, "w") as f:
        json.dump(payload, f, indent=1)

    def line(label, t, interval):
        pct = f"{t['win_pct']:.1f}%" if t["win_pct"] is not None else "—"
        rng = f"  [{interval[0]}–{interval[1]}%]" if interval else ""
        mnf = (f"{t['mnf_win']}-{t['mnf_loss'] + t['mnf_push']}"
               if t["mnf_graded"] else "—")
        print(f"  {label:10} {t['win']}-{t['loss']}"
              + (f"-{t['push']}p" if t["push"] else "")
              + f"   {pct}{rng}   MNF {mnf}")

    s = payload["season"]
    print(f"\nSCORECARD  ({eng_t['graded']} picks graded each)\n")
    line("Engine", eng_t, s["engine_interval"])
    line("You", aus_t, s["austin_interval"])
    print(f"\n  Perfect weeks — engine {s['engine_perfect_weeks']}, "
          f"you {s['austin_perfect_weeks']}")
    h = payload["head_to_head"]
    print(f"\n  Agreed on {h['agreed']} picks, disagreed on {h['disagreed']}.")
    if h["disagreed"]:
        de, da = h["engine_on_disagreements"], h["austin_on_disagreements"]
        print(f"  Where you split: engine {de['win']}-{de['loss'] + de['push']}, "
              f"you {da['win']}-{da['loss'] + da['push']}  "
              f"<- the only comparison that isolates judgement")
    if s["weeks_missing_your_picks"]:
        print(f"\n  !! No picks recorded for weeks "
              f"{s['weeks_missing_your_picks']} — those weeks cannot be scored.")
    if calib:
        print(f"\n  CALIBRATION — does price actually pay?")
        for lo, hi, label in VALUE_BUCKETS:
            c = calib.get(label)
            if not c or not c["n"]:
                continue
            print(f"    {label:10} n={c['n']:<4} predicted {c['predicted_pct']}%  "
                  f"actual {c['actual_pct']}%  [{c['interval'][0]}–{c['interval'][1]}%]")
    print(f"\n  {payload['sample_size_note']['text']}\n")
    print(f"  wrote {args.out}")


if __name__ == "__main__":
    main()
