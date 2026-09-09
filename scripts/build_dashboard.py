#!/usr/bin/env python3
"""Render the dashboard page from collected data + analyst notes.

  python scripts/build_dashboard.py [--notes notes.json] [--out dashboard.html]
                                    [--banner "text"] [--digest "html"]

Artifacts are published as a fragment (the host supplies doctype/head/body), so
the template is a fragment and this script only injects the data blob.
"""
import argparse
import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "scripts", "dashboard_template.html")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", default=os.path.join(ROOT, "data", "games.json"))
    ap.add_argument("--notes", default=os.path.join(ROOT, "data", "notes.json"))
    ap.add_argument("--context", default=os.path.join(ROOT, "data", "context.json"))
    ap.add_argument("--picks", default=os.path.join(ROOT, "data", "picks.json"))
    ap.add_argument("--scorecard", default=os.path.join(ROOT, "data", "scorecard.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "dashboard.html"))
    ap.add_argument("--banner", default="")
    ap.add_argument("--digest", default="")
    args = ap.parse_args()

    with open(args.games) as f:
        games = json.load(f)

    notes = {}
    if os.path.exists(args.notes):
        with open(args.notes) as f:
            notes = json.load(f)

    context = {}
    if os.path.exists(args.context):
        with open(args.context) as f:
            context = (json.load(f) or {}).get("games", {})

    picks = None
    if os.path.exists(args.picks):
        with open(args.picks) as f:
            picks = json.load(f)

    scorecard = None
    if os.path.exists(args.scorecard):
        with open(args.scorecard) as f:
            scorecard = json.load(f)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "games": games,
        "notes": notes,
        "context": context,
        "picks": picks,
        "scorecard": scorecard,
        "banner": args.banner,
        "digest": args.digest,
    }

    with open(TEMPLATE) as f:
        html = f.read()

    blob = json.dumps(payload, separators=(",", ":"))
    # Guard against a stray closing tag inside the data ending the script early.
    blob = blob.replace("</", "<\\/")
    if "/*__DATA__*/ null" not in html:
        raise SystemExit("template is missing the /*__DATA__*/ null placeholder")
    html = html.replace("/*__DATA__*/ null", blob, 1)

    with open(args.out, "w") as f:
        f.write(html)

    kb = os.path.getsize(args.out) / 1024
    print(f"wrote {args.out}  games={len(games)}  notes={len(notes)}  "
          f"context={len(context)}  picks={len(picks['picks']) if picks else 0}  "
          f"size={kb:.0f}KB")


if __name__ == "__main__":
    main()
