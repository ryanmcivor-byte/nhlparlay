"""Due for One: proven scorers in a goal drought who are still shooting.

A skater qualifies when:
  * he's a real scorer: at least MIN_RATE goals per game last season (25-goal pace),
  * he's in a real drought: MIN_GAMES+ straight games without a goal, long enough
    that he'd normally have scored MIN_OWED goals in that span,
  * and he's still shooting: at least MIN_SHOTS shots a game in the drought, and at
    least SHOT_RATIO of his usual shot rate (unlucky, not cold).
His "goals owed" (drought games x normal rate) ranks him; shooting more than usual
in the drought adds a bonus.
"""
from concurrent.futures import ThreadPoolExecutor

import facts
import nhl

MIN_RATE = 0.30
MIN_GAMES = 4
MIN_OWED = 1.5
MIN_SHOTS = 2.0
SHOT_RATIO = 0.75   # 2.5 a game when he usually takes 3.2 is still shooting; below 75% he has gone cold
MIN_GP = 20        # last season needs this many games to trust his rate


def _baseline(ld, season):
    """(goals per game, shots per game) from last season, or the two before it combined."""
    rows = {r["season"]: r for r in facts.nhl_seasons(ld)}
    g = s = gp = 0
    for yr in (season - 10001, season - 20002):
        r = rows.get(yr)
        if r:
            g += r.get("goals") or 0
            s += r.get("shots") or 0
            gp += r.get("gamesPlayed") or 0
        if gp >= MIN_GP:
            break
    if gp < MIN_GP:
        return None
    return g / gp, s / gp


def _drought(pid, season, date):
    """(games, shots, last goal date) for his current scoreless run before `date`."""
    games = [g for g in nhl.game_log(pid, season) + nhl.game_log(pid, season - 10001)
             if g.get("gameDate", "9999") < date]
    games.sort(key=lambda g: g["gameDate"], reverse=True)
    n = shots = 0
    for g in games:
        if (g.get("goals") or 0) > 0:
            return n, shots, g["gameDate"]
        n += 1
        shots += g.get("shots") or 0
    return n, shots, None


def facts_for(players, season, date):
    """{skater id: fact} for tonight's skaters who are due."""
    cands = []
    for p in players:
        base = _baseline(p["ld"], season)
        if base and base[0] >= MIN_RATE:
            cands.append((p, base))

    def check(c):
        p, (rate, spg) = c
        try:
            n, shots, last = _drought(p["id"], season, date)
        except Exception:
            return None
        if n < MIN_GAMES or n * rate < MIN_OWED or not n:
            return None
        per = shots / n
        if per < MIN_SHOTS or per < SHOT_RATIO * spg:
            return None
        owed = n * rate
        heat = min(1.5, per / spg) if spg else 1.0
        since = f" (last goal {last})" if last else ""
        return p["id"], {
            "kind": "due", "emoji": "🧊", "title": f"Due: {n} games without a goal",
            "text": f"No goals in his last {n} games{since}, but {shots} shots ({per:.1f} a game vs his usual "
                    f"{spg:.1f}). At his normal {rate:.2f} goals a game he'd have about {owed:.1f} by now.",
            "chaos": min(10, 5 + round(owed)),
            "owed": round(owed, 2), "score": round(owed * heat, 3)}

    out = {}
    with ThreadPoolExecutor(12) as ex:
        for r in ex.map(check, cands):
            if r:
                out[r[0]] = r[1]
    return out
