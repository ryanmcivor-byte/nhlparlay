"""The Scammer's Best Value Picks: a value model for anytime-goalscorer bets.

For every skater with a DraftKings price, the model estimates his chance to
score by blending two signals:

    p = sigmoid(w0 + w1 * logit(our goal %) + w2 * logit(book's implied %))

The book's price is the strongest signal (and carries the vig, which the
intercept learns to strip out); our own rate can nudge it. A bet's value is
its expected return per $1: p * decimal_odds - 1. Each night the 7 best values
among players the model gives at least a MIN_CHANCE shot are picked (straight
bets, not a parlay). Backtests showed longshot "value" is mostly model error.

It improves every day: each morning's prices and model numbers are saved to
data/odds/<date>.json; once the games are final, who scored is filled in, and
the weights are refit on every bet seen so far (regularized toward a sensible
starting point, so a few nights can't send it off the rails). The walk-forward
log loss of the model vs the book alone is kept in data/scammer_model.json, so
it's honest about whether the blend is actually beating the market.
"""
import glob
import json
import math
import os
import re
import unicodedata
from datetime import date as Date, timedelta

import nhl

PICKS = 7
MIN_CHANCE = 0.20   # only real scorers: books overcharge longshots most, and our rough rate overrates them
ROOT = os.path.dirname(os.path.abspath(__file__))
ODDS_DIR = os.path.join(ROOT, "data", "odds")
MODEL = os.path.join(ROOT, "data", "scammer_model.json")
PRIOR = [-0.15, 0.20, 0.90]     # start: mostly the book (vig trimmed), a little of our rate
PRIOR_STRENGTH = 25.0           # how hard the fit is pulled back toward PRIOR
PLAYING = ("confirmed", "projected")


# ---------------------------------------------------------------------------
# odds math
# ---------------------------------------------------------------------------
def implied(am):
    return 100.0 / (am + 100.0) if am > 0 else -am / (-am + 100.0)


def decimal(am):
    return 1 + am / 100.0 if am > 0 else 1 + 100.0 / -am


def _logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _sigmoid(z):
    return 1 / (1 + math.exp(-z)) if z > -50 else 0.0


def _x(p_model, am):
    return [1.0, _logit(p_model), _logit(implied(am))]


def predict(w, p_model, am):
    return _sigmoid(sum(a * b for a, b in zip(w, _x(p_model, am))))


# ---------------------------------------------------------------------------
# fitting (regularized logistic regression by Newton's method; 3 weights)
# ---------------------------------------------------------------------------
def _solve(a, b):
    """Solve a 3x3 (or nxn) linear system a·x = b by Gaussian elimination."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        for r in range(n):
            if r != c and m[c][c]:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] for i in range(n)]


def fit(rows, prior=PRIOR, strength=PRIOR_STRENGTH, cols=(0, 1, 2)):
    """rows: [(p_model, american, scored 0/1)] -> weights (unused cols stay 0)."""
    w = [prior[c] if c in cols else 0.0 for c in range(3)]
    xs = [(_x(pm, am), y) for pm, am, y in rows]
    for _ in range(25):
        g = [strength * (w[c] - prior[c]) if c in cols else 0.0 for c in range(3)]
        h = [[(strength if (i == j and i in cols) else 0.0) for j in range(3)] for i in range(3)]
        for x, y in xs:
            p = _sigmoid(sum(a * b for a, b in zip(w, x)))
            for i in cols:
                g[i] += (p - y) * x[i]
                for j in cols:
                    h[i][j] += p * (1 - p) * x[i] * x[j]
        idx = list(cols)
        step = _solve([[h[i][j] for j in idx] for i in idx], [g[i] for i in idx])
        for k, i in enumerate(idx):
            w[i] -= step[k]
        if max(abs(s) for s in step) < 1e-7:
            break
    return w


def _logloss(w, rows):
    if not rows:
        return None
    s = 0.0
    for pm, am, y in rows:
        p = min(max(predict(w, pm, am), 1e-6), 1 - 1e-6)
        s -= y * math.log(p) + (1 - y) * math.log(1 - p)
    return s / len(rows)


def load_model():
    try:
        with open(MODEL) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"weights": PRIOR, "n": 0, "days": 0}


# ---------------------------------------------------------------------------
# matching ESPN's priced athletes to tonight's NHL players
# ---------------------------------------------------------------------------
def _norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    return re.sub(r"[^a-z]", "", "".join(c for c in s if not unicodedata.combining(c)).lower())


def attach_odds(players, games, date, opening=False):
    """{player id: american} for players with an anytime-goalscorer price.
    players need: id, name, last, team, ld (landing, for birthDate).
    opening=True uses the opening price (for rebuilding past days)."""
    by_pair = {(g["away"], g["home"]): g for g in games}
    out = {}
    try:
        events = nhl.espn_games(date)
    except Exception:
        return out
    for ev in events:
        if (ev["away"], ev["home"]) not in by_pair:
            continue
        try:
            props = nhl.espn_props(ev["id"])
            roster = nhl.espn_roster(ev["homeId"]) + nhl.espn_roster(ev["awayId"])
        except Exception:
            continue
        espn = {r["espnId"]: r for r in roster}
        mine = [p for p in players if p["team"] in (ev["home"], ev["away"])]
        by_name = {_norm(p["name"]): p for p in mine}
        for aid, price in props.items():
            r = espn.get(aid)
            if not r:
                continue
            p = by_name.get(_norm(r["name"]))
            if p is None and r["dob"]:   # name spelled differently: same birthday + last name
                p = next((q for q in mine if (q.get("ld") or {}).get("birthDate") == r["dob"]
                          and _norm(q["last"]) in _norm(r["name"])), None)
            if p is not None:
                out[p["id"]] = price["open"] if opening else price["american"]
    return out


# ---------------------------------------------------------------------------
# tonight's picks
# ---------------------------------------------------------------------------
def choose(slate, n=PICKS):
    """The n best-value bets among priced skaters in tonight's lineup."""
    w = load_model()["weights"]
    cands = []
    for p in slate["players"]:
        am = p.get("odds")
        if not am or (p.get("lineup") or {}).get("status") not in PLAYING:
            continue
        pf = predict(w, p["prob"], am)
        ev = pf * decimal(am) - 1
        cands.append((ev, pf, am, p))
    cands.sort(key=lambda c: -c[0])
    real = [c for c in cands if c[1] >= MIN_CHANCE]
    picks = (real + [c for c in cands if c[1] < MIN_CHANCE])[:n]   # small slates: fill with the next best
    legs = []
    for ev, pf, am, p in picks:
        price = f"+{am}" if am > 0 else str(am)
        edge = "an edge" if ev > 0 else "no edge, best of the rest"
        legs.append({"id": p["id"], "name": p["name"], "team": p["team"], "opp": p["opp"], "home": p["home"],
                     "gameId": p["gameId"], "headshot": p.get("headshot"), "prob": round(pf, 4),
                     "american": am, "ev": round(ev, 4),
                     "fact": {"kind": "value", "emoji": "💸",
                              "title": f"Value bet at {price}" if ev > 0 else f"Best price on the board: {price}",
                              "text": f"Model {pf:.0%} vs book {implied(am):.0%}: {ev:+.0%} expected return "
                                      f"per $1 ({edge})."}})
    return legs


def snapshot(slate):
    """Save every priced, playing skater's model number and price for training."""
    rows = [{"id": p["id"], "name": p["name"], "team": p["team"], "gameId": p["gameId"],
             "pModel": p["prob"], "american": p["odds"]}
            for p in slate["players"] if p.get("odds") and (p.get("lineup") or {}).get("status") in PLAYING]
    if not rows:
        return
    os.makedirs(ODDS_DIR, exist_ok=True)
    path = os.path.join(ODDS_DIR, f"{slate['date']}.json")
    if os.path.exists(path):
        return            # the morning snapshot is the one that counts
    with open(path, "w") as fh:
        json.dump({"date": slate["date"], "rows": rows}, fh, indent=0)


# ---------------------------------------------------------------------------
# learning: fill in who scored, refit, and keep an honest scorecard
# ---------------------------------------------------------------------------
def grade_snapshots(today=None):
    """Fill in "scored" (1/0, or None if he didn't dress) for finished days."""
    today = (today or nhl.today_et()).isoformat()
    for path in sorted(glob.glob(os.path.join(ODDS_DIR, "*.json"))):
        with open(path) as fh:
            snap = json.load(fh)
        if snap["date"] >= today or all("scored" in r for r in snap["rows"]):
            continue
        boxes, final = {}, True
        for gid in {r["gameId"] for r in snap["rows"]}:
            try:
                b = nhl.boxscore(gid)
            except Exception:
                b = None
            if not b or b.get("gameState") not in ("OFF", "FINAL"):
                final = False
            boxes[gid] = b or {}
        if not final:
            continue
        for r in snap["rows"]:
            line = _line(boxes[r["gameId"]], r["id"])
            r["scored"] = None if line is None else int((line.get("goals") or 0) > 0)
        with open(path, "w") as fh:
            json.dump(snap, fh, indent=0)


def _line(box, pid):
    pbg = box.get("playerByGameStats") or {}
    for side in ("awayTeam", "homeTeam"):
        for grp in ("forwards", "defense"):
            for p in (pbg.get(side) or {}).get(grp) or []:
                if p.get("playerId") == pid:
                    return p
    return None


def _training_days():
    days = []
    for path in sorted(glob.glob(os.path.join(ODDS_DIR, "*.json"))):
        with open(path) as fh:
            snap = json.load(fh)
        rows = [(r["pModel"], r["american"], r["scored"]) for r in snap["rows"] if r.get("scored") is not None]
        if rows:
            days.append((snap["date"], rows))
    return days


def train():
    """Refit on every graded bet; score each day out-of-sample (fit only on the days before it)."""
    days = _training_days()
    oos_model, oos_book, oos_n = 0.0, 0.0, 0
    history = []
    for i, (d, rows) in enumerate(days):
        past = [r for _, rs in days[:i] for r in rs]
        w_full = fit(past)
        w_book = fit(past, cols=(0, 2), prior=[PRIOR[0], 0.0, 1.0])   # the book alone, vig trimmed
        oos_model += _logloss(w_full, rows) * len(rows)
        oos_book += _logloss(w_book, rows) * len(rows)
        oos_n += len(rows)
        history.append({"date": d, "weights": [round(x, 4) for x in w_full], "bets": len(rows)})
    # how the actual pick rule would have done, each day using only the days before it
    wins = losses = 0
    units = 0.0
    for i, (d, rows) in enumerate(days):
        w_i = fit([r for _, rs in days[:i] for r in rs])
        scored = [(predict(w_i, pm, am) * decimal(am) - 1, predict(w_i, pm, am), am, y) for pm, am, y in rows]
        scored.sort(key=lambda c: -c[0])
        top = ([c for c in scored if c[1] >= MIN_CHANCE] + [c for c in scored if c[1] < MIN_CHANCE])[:PICKS]
        for _, _, am, y in top:
            wins += y
            losses += 1 - y
            units += (decimal(am) - 1) if y else -1
    market = [(decimal(am) - 1) if y else -1 for _, rs in days for _, am, y in rs]
    all_rows = [r for _, rs in days for r in rs]
    w = fit(all_rows)
    model = {"weights": [round(x, 5) for x in w], "n": len(all_rows), "days": len(days),
             "trainedThrough": days[-1][0] if days else None,
             "scoringRate": round(sum(r[2] for r in all_rows) / len(all_rows), 4) if all_rows else None,
             "backtest": {"wins": wins, "losses": losses, "units": round(units, 2),
                          "roi": round(units / (wins + losses), 4) if wins + losses else None,
                          "marketRoi": round(sum(market) / len(market), 4) if market else None},
             "walkForward": {"bets": oos_n,
                             "modelLogLoss": round(oos_model / oos_n, 5) if oos_n else None,
                             "bookLogLoss": round(oos_book / oos_n, 5) if oos_n else None},
             "history": history[-60:]}
    os.makedirs(os.path.dirname(MODEL), exist_ok=True)
    with open(MODEL, "w") as fh:
        json.dump(model, fh, indent=1)
    return model


def backfill(start, end, build_as_of):
    """Rebuild missing morning snapshots for past days (opening prices, rolled-back numbers)."""
    d, end = Date.fromisoformat(start), Date.fromisoformat(end)
    while d <= end:
        path = os.path.join(ODDS_DIR, f"{d.isoformat()}.json")
        if not os.path.exists(path):
            s = build_as_of(d.isoformat())
            if s.get("players"):
                snapshot(s)
        d += timedelta(days=1)
