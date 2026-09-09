#!/usr/bin/env python3
"""Offline test of pick grading and the scorecard. No network.

Grading errors would be silent and would corrupt the only feedback loop this
system has, so every branch is asserted: covers, fails, pushes (which lose),
Monday-night splits, pending games, unreported weeks, and the disagreement
isolation that is the whole point of the head-to-head.
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger as L  # noqa: E402

FAILS = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}" + ("" if ok else f" want {want!r}"))
    if not ok:
        FAILS.append(label)


def pk(gid, side, line, val=1.0, wp=0.55, mnf=False):
    return {"game_id": gid, "matchup": gid, "team": gid, "side": side,
            "pool_line": line, "line_value": val, "win_prob": wp, "is_mnf": mnf}


def res(margin):
    return {"home_margin": margin, "week": 1, "away_team": "X", "home_team": "Y",
            "away_score": 0, "home_score": 0}


def main():
    print("grading, one pick at a time")
    R = {"a": res(7), "b": res(-7), "c": res(0), "d": res(3)}
    check("home laying 3, wins by 7 -> covers",
          L.grade_pick(pk("a", "home", -3.0), R["a"]), "win")
    check("home laying 3, loses by 7 -> fails",
          L.grade_pick(pk("b", "home", -3.0), R["b"]), "loss")
    check("away getting 3, home loses by 7 -> covers",
          L.grade_pick(pk("b", "away", 3.0), R["b"]), "win")
    check("home laying 3, wins by exactly 3 -> push",
          L.grade_pick(pk("d", "home", -3.0), R["d"]), "push")
    check("away getting 3, home wins by exactly 3 -> push",
          L.grade_pick(pk("d", "away", -3.0), R["d"]), "push")
    check("pick'em, tie game -> push",
          L.grade_pick(pk("c", "home", 0.0), R["c"]), "push")
    check("game not played -> pending", L.grade_pick(pk("z", "home", -3.0), None), None)

    print("\na push is a loss, but stays visible")
    t = L.tally([pk("a", "home", -3.0), pk("d", "home", -3.0)], R)
    check("push not counted as a win", t["win"], 1)
    check("push tracked separately", t["push"], 1)
    check("push sits in the denominator", t["graded"], 2)
    check("win pct treats push as a loss", t["win_pct"], 50.0)

    print("\nperfect week")
    check("a push breaks a perfect week",
          L.week_perfect([pk("a", "home", -3.0), pk("d", "home", -3.0)], R), False)
    check("all wins is perfect",
          L.week_perfect([pk("a", "home", -3.0), pk("b", "away", 3.0)], R), True)
    check("pending game -> unknown, not False",
          L.week_perfect([pk("a", "home", -3.0), pk("z", "home", -3.0)], R), None)

    print("\nMonday night tracked apart from the rest")
    t = L.tally([pk("a", "home", -3.0, mnf=True), pk("b", "home", -3.0)], R)
    check("MNF win counted", t["mnf_win"], 1)
    check("non-MNF loss not counted as MNF", t["mnf_loss"], 0)
    check("MNF denominator", t["mnf_graded"], 1)

    print("\nfull scorecard: agreement, disagreement, unreported week")
    tmp = tempfile.mkdtemp()
    lg = {"weeks": {
        "1": {"austin_source": "own",
              "engine": [pk("a", "home", -3.0), pk("d", "home", -3.0, mnf=True)],
              "austin": [pk("a", "home", -3.0), pk("d", "away", -3.0, mnf=True)]},
        "2": {"engine": [pk("b", "home", -3.0)], "austin": []},
    }}
    lp, rp = os.path.join(tmp, "l.json"), os.path.join(tmp, "r.json")
    op = os.path.join(tmp, "s.json")
    json.dump(lg, open(lp, "w")); json.dump(R, open(rp, "w"))
    sys.argv = ["ledger.py", "--ledger", lp, "--results", rp, "--out", op]
    import io, contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        L.main()
    sc = json.load(open(op))

    check("agreed picks counted", sc["head_to_head"]["agreed"], 1)
    check("opposite sides on one game = one disagreement",
          sc["head_to_head"]["disagreed"], 1)
    check("engine result on the disagreement",
          sc["head_to_head"]["engine_on_disagreements"]["push"], 1)
    check("austin result on the disagreement",
          sc["head_to_head"]["austin_on_disagreements"]["push"], 1)
    check("week with no reported picks is flagged",
          sc["season"]["weeks_missing_your_picks"], [2])
    check("engine graded across both weeks", sc["season"]["engine"]["graded"], 3)
    check("austin graded only where he reported", sc["season"]["austin"]["graded"], 2)
    check("interval reported at tiny n", bool(sc["season"]["engine_interval"]), True)
    check("sample-size warning present",
          sc["sample_size_note"]["picks_needed_to_separate_55_from_50"] > 700, True)

    shutil.rmtree(tmp)
    print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILURES: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
