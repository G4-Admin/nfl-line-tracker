#!/usr/bin/env python3
"""Margin model: converts points of line value into a probability of covering.

REIMPLEMENTATION, 2026-09-09. The original pool_edge.py was written 2026-09-08
and never committed to the repo; it is not recoverable. This is a rebuild from
the specification in claude/pool-strategy.md and the engine notes in
claude/ledger-runbook.md. Its two calibrated inputs are NOT measured against
this pool's results -- they come from public NFL margin data -- so every number
this produces is a model output, not an observation.

What is modelled
----------------
NFL final margins are not smooth. They pile up on 3 and 7 and, less sharply, on
4, 6, 10 and 14. A normal curve through them prices half points badly exactly
where half points matter most. So the distribution here is a discrete normal
over integer margins, reweighted at the key numbers, renormalised.

The two inputs that are assumptions, stated plainly:

  SIGMA     the spread of final margins around the market's number.
  KEY_MASS  how much extra mass sits on each key number.

The one check available on them is in claude/pool-strategy.md: a game sitting on
a whole number with no line value behind it should price near 47%, because a
push loses in this pool. Run this file directly to see that check.

Why the market line is a MEDIAN, not a mean
-------------------------------------------
The engine notes record this as a live bug found on 2026-09-08: treating the
market number as the mean of the margin distribution priced a fair -3.5 at 48.3%
instead of a coinflip. The spike at 3 sits just below 3.5, and centring the mean
on 3.5 hands that entire spike to the underdog for free. `calibrate_mu` instead
solves for the centre that puts the market's own number at the MEDIAN --
P(margin > line) == P(margin < line) -- which is what "the market thinks this
game is a coinflip at this number" actually means.

Sign convention, everywhere: spreads are quoted from the HOME side. -3.5 means
the home team lays 3.5. A margin is home points minus away points.

THIS FILE REPLACED the 2026-09-08 original in the repo on 2026-10-13. The two
share no symbol: the original defined SD/KEY_BUMPS and
analyse/cover_probability/perfect_five, this one defines SIGMA/KEY_MASS and
price/best_side/calibrate_mu. make_picks.py and validate_sheet.py were migrated
in the same commit for that reason.
"""

import math

SIGMA = 13.5

KEY_MASS = {3: 2.42, 4: 1.12, 6: 1.18, 7: 1.58, 10: 1.28, 13: 1.08, 14: 1.18, 17: 1.10}

LIMIT = 70


def margin_pmf(mu, sigma=SIGMA, key_mass=None):
    """Discrete distribution over integer home margins, centred near `mu`."""
    key_mass = KEY_MASS if key_mass is None else key_mass
    pmf, total = {}, 0.0
    for m in range(-LIMIT, LIMIT + 1):
        z = (m - mu) / sigma
        p = math.exp(-0.5 * z * z)
        p *= key_mass.get(abs(m), 1.0)
        pmf[m] = p
        total += p
    for m in pmf:
        pmf[m] /= total
    return pmf


def _split(pmf, t):
    """-> (P(margin > t), P(margin == t), P(margin < t))."""
    above = below = at = 0.0
    for m, p in pmf.items():
        if m > t:
            above += p
        elif m < t:
            below += p
        else:
            at += p
    return above, at, below


def calibrate_mu(t, sigma=SIGMA, key_mass=None, tol=1e-9):
    """Centre that makes `t` the median of the margin distribution."""
    lo, hi = t - 12.0, t + 12.0
    for _ in range(200):
        mid = (lo + hi) / 2
        above, _at, below = _split(margin_pmf(mid, sigma, key_mass), t)
        if abs(above - below) < tol:
            return mid
        if above < below:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def price(market_line, pool_line, sigma=SIGMA, key_mass=None):
    """Probability each side of `pool_line` covers, given the market's number."""
    t_market = -float(market_line)
    t_pool = -float(pool_line)
    mu = calibrate_mu(t_market, sigma, key_mass)
    pmf = margin_pmf(mu, sigma, key_mass)
    above, at, below = _split(pmf, t_pool)
    return {
        "mu": mu,
        "home_win": above,
        "away_win": below,
        "push": at,
        "on_key": abs(t_pool) in (3, 7) and float(t_pool).is_integer(),
        "line_value_home": round(float(pool_line) - float(market_line), 2),
    }


def best_side(market_line, pool_line, **kw):
    """The better side of this pool number, with its probability and price."""
    p = price(market_line, pool_line, **kw)
    if p["home_win"] >= p["away_win"]:
        side, win = "home", p["home_win"]
    else:
        side, win = "away", p["away_win"]
    value = p["line_value_home"] if side == "home" else -p["line_value_home"]
    return {
        "side": side,
        "win_prob": win,
        "line_value": round(value, 2),
        "push": p["push"],
        "on_key": p["on_key"],
        "mu": p["mu"],
    }


def anchor_check(verbose=True):
    """The one check available on SIGMA and KEY_MASS."""
    rows = []
    fair3 = price(-3.0, -3.0)
    rows.append(("fair line on 3, no value", fair3["home_win"], 0.47,
                 "strategy doc: ~47%, a push loses"))
    fair35 = price(-3.5, -3.5)
    rows.append(("fair line on 3.5, no value", fair35["home_win"], 0.50,
                 "no push is possible, so this must be a coinflip"))
    onto3 = price(-3.5, -3.0)
    rows.append(("bought 3.5 down to 3", onto3["home_win"], 0.50,
                 "strategy doc: 'roughly a wash'"))
    onto35 = price(-3.0, -3.5)
    rows.append(("dog bought from +3 to +3.5", onto35["away_win"], None,
                 "gains exactly the push mass at 3"))
    fair7 = price(-7.0, -7.0)
    rows.append(("fair line on 7, no value", fair7["home_win"], 0.48,
                 "same effect as 3, weaker"))
    half = price(-6.5, -6.5)
    rows.append(("fair line on 6.5, no value", half["home_win"], 0.50, "no push possible"))
    full = price(-3.5, -2.5)
    rows.append(("a full point of price, off a key number", full["home_win"], None,
                 "lay 2.5 when the market says 3.5"))
    dead = price(-4.5, -4.0)
    rows.append(("lay 4.0 when the market says 4.5", dead["home_win"], 0.50,
                 "WORTH NOTHING: same games covered, new push at 4 loses"))
    livehalf = price(-4.0, -3.5)
    rows.append(("lay 3.5 when the market says 4.0", livehalf["home_win"], None,
                 "worth the mass at 4: a push became a win"))

    if verbose:
        print(f"SIGMA={SIGMA}  KEY_MASS={KEY_MASS}")
        print(f"P(margin lands exactly on 3 | fair line of 3) = "
              f"{price(-3.0, -3.0)['push']:.4f}")
        print()
        print(f"{'case':<42} {'model':>7} {'expected':>9}   note")
        for name, got, want, note in rows:
            w = "-" if want is None else f"{want:.3f}"
            print(f"{name:<42} {got:>7.3f} {w:>9}   {note}")
    return rows


if __name__ == "__main__":
    anchor_check()
