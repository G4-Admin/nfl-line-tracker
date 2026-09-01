#!/usr/bin/env python3
"""Rebuild data/lines_master.xlsx from data/games.json.

Two sheets:
  Summary    one row per game - opener, current/closing, total movement, key-number cross
  Daily      one row per game per day - the consensus line and the book spread that day
"""
import json
import os
from datetime import datetime, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

HEAD_FILL = PatternFill("solid", fgColor="1F2937")
HEAD_FONT = Font(color="FFFFFF", bold=True)


def fmt_line(team_home, team_away, spread):
    """A spread of -3.5 means the home team is laying 3.5."""
    if spread is None:
        return ""
    if spread == 0:
        return "PK"
    fav = team_home if spread < 0 else team_away
    return f"{fav} {-abs(spread)}"


def style_header(ws, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill, cell.font = HEAD_FILL, HEAD_FONT
        cell.alignment = Alignment(horizontal="left")
    ws.freeze_panes = "A2"


def autosize(ws):
    for col in ws.columns:
        width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(width + 3, 42)


def main():
    with open(os.path.join(DATA, "games.json")) as f:
        games = json.load(f)

    wb = Workbook()

    summary = wb.active
    summary.title = "Summary"
    summary.append([
        "Week", "Kickoff (UTC)", "Away", "Home", "Opened", "Open line",
        "Current line", "Move (pts)", "Toward", "Crossed 3", "Crossed 7",
        "Days tracked", "Books", "Open source", "Game ID",
    ])

    daily = wb.create_sheet("Daily")
    daily.append([
        "Date", "Week", "Away", "Home", "Kickoff (UTC)", "Consensus (home)",
        "Line", "DraftKings", "FanDuel", "BetMGM", "Caesars",
        "Books", "Low", "High", "Book spread", "Chg vs prior", "Chg vs open",
    ])

    ordered = sorted(games.values(), key=lambda g: (g.get("commence_time") or "", g["home_team"]))

    for g in ordered:
        home, away = g["home_team"], g["away_team"]
        op = g["opened"]["consensus"]
        cur = g["current"]["consensus"]
        move = round(cur - op, 1)
        crossed3 = "yes" if (abs(op) < 3 <= abs(cur)) or (abs(cur) < 3 <= abs(op)) else ""
        crossed7 = "yes" if (abs(op) < 7 <= abs(cur)) or (abs(cur) < 7 <= abs(op)) else ""
        toward = "" if move == 0 else (home if move < 0 else away)

        summary.append([
            g.get("week"), g.get("commence_time"), away, home,
            g["opened"]["date"], op, cur, move, toward, crossed3, crossed7,
            len(g["history"]), g["current"].get("n_books"),
            g["opened"].get("source", ""), g["game_id"],
        ])

        prev = None
        for p in g["history"]:
            lo, hi = p.get("min"), p.get("max")
            daily.append([
                p["date"], g.get("week"), away, home, g.get("commence_time"),
                p["consensus"], fmt_line(home, away, p["consensus"]),
                p.get("draftkings"), p.get("fanduel"), p.get("betmgm"),
                p.get("williamhill_us"), p.get("n_books"), lo, hi,
                round(hi - lo, 1) if lo is not None and hi is not None else None,
                None if prev is None else round(p["consensus"] - prev, 1),
                round(p["consensus"] - op, 1),
            ])
            prev = p["consensus"]

    for ws, n in ((summary, 15), (daily, 17)):
        style_header(ws, n)
        autosize(ws)

    meta = wb.create_sheet("About")
    for row in [
        ["NFL line tracker - master spreadsheet"],
        ["Generated (UTC)", datetime.now(timezone.utc).isoformat(timespec="seconds")],
        ["Games", len(games)],
        [],
        ["Convention", "Spreads are quoted from the HOME team's side."],
        ["", "-3.5 means the home team is favored by 3.5."],
        ["", "A negative Move means the line went toward the home team."],
        ["Consensus", "Median point spread across all available US books."],
        ["Open source", "baseline_at_setup = line was already on the board when tracking began."],
        ["", "first_capture = the first line we ever saw for this game, i.e. a true opener."],
    ]:
        meta.append(row)
    meta["A1"].font = Font(bold=True, size=13)
    autosize(meta)

    out = os.path.join(DATA, "lines_master.xlsx")
    wb.save(out)
    print(f"wrote {out}  games={len(games)}  daily_rows={daily.max_row - 1}")


if __name__ == "__main__":
    main()
