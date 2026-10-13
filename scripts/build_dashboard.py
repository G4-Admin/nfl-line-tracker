#!/usr/bin/env python3
"""Render the board from collected data + notes + baselines + context.

Local build, 2026-09-08. The template used here is the live one recovered from
the published artifact with its data blob swapped back to the
/*__DATA__*/ null placeholder.

THIS FILE REPLACED the repo's older copy on 2026-10-13, with pool_edge.py,
make_picks.py and validate_sheet.py. The version it replaces still accepted
--picks and --scorecard (both long gone), had no --baselines, --venues or
--teams, and would have published a board missing every one of those.

NOTE ON --template: the default below names dashboard_template_live.html, which
does not exist in this repo, so running without --template fails loudly. That is
deliberate and must not be "fixed" by pointing it at scripts/dashboard_template.html
-- that file is the 2026-09-08 original, roughly a fifth the size of the live
template, and building from it silently strips the pool boxes, the Matchups view,
week navigation and applySnapshot. The daily run recovers the real template from
the published artifact and passes it explicitly. A loud failure is the correct
behaviour here; a silent wrong template is this project's house failure mode.
"""
import argparse
import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "scripts", "dashboard_template_live.html")


def load(path, default):
    if path and os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", required=True)
    ap.add_argument("--notes")
    ap.add_argument("--context")
    ap.add_argument("--baselines")
    ap.add_argument("--venues")
    ap.add_argument("--teams")
    ap.add_argument("--books-floor", type=int, default=3)
    ap.add_argument("--out", required=True)
    ap.add_argument("--banner", default="")
    ap.add_argument("--digest", default="")
    ap.add_argument("--template", default=TEMPLATE)
    args = ap.parse_args()

    with open(args.games) as f:
        games = json.load(f)
    notes = load(args.notes, {})
    context = load(args.context, {})
    baselines = load(args.baselines, {})
    venues = load(args.venues, {})
    teams = load(args.teams, {})

    if isinstance(context, dict) and "games" in context and "generated_at" in context:
        context = context["games"]

    dates = [g["current"]["date"] for g in games.values()
             if g.get("current") and g["current"].get("date")]
    data_date = max(dates) if dates else None

    try:
        import pool_edge
        model = {"sigma": pool_edge.SIGMA, "key_mass": pool_edge.KEY_MASS,
                 "note": "Reimplementation of 2026-09-09; constants from public NFL "
                         "margin data, not from this pool's results."}
    except Exception:
        model = None

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_date": data_date,
        "books_floor": args.books_floor,
        "model": model,
        "games": games,
        "notes": notes,
        "context": context,
        "baselines": baselines,
        "venues": venues,
        "teams": teams,
        "banner": args.banner,
        "digest": args.digest,
    }

    with open(args.template) as f:
        html = f.read()
    if "/*__DATA__*/ null" not in html:
        raise SystemExit("template is missing the /*__DATA__*/ null placeholder")

    blob = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    html = html.replace("/*__DATA__*/ null", blob, 1)

    with open(args.out, "w") as f:
        f.write(html)

    today = datetime.now(timezone.utc).date().isoformat()
    stale = "" if data_date == today else f"  STALE(data_date={data_date}, today={today})"
    kb = os.path.getsize(args.out) / 1024
    print(f"wrote {args.out}  games={len(games)}  notes={len(notes)}  "
          f"baselines={len(baselines)}  context={len(context)}  "
          f"venues={len(venues)}  teams={len(teams)}  "
          f"data_date={data_date}  size={kb:.0f}KB{stale}")
    if model is None:
        print("  !! model is None -- pool_edge was not importable. The page cannot "
              "reprice and its self-check will not run. Set PYTHONPATH and rebuild.")


if __name__ == "__main__":
    main()
