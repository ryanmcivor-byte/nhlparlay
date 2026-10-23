"""Game Simulations: play each of a day's games 10 times and pick the one to show.

Each team's expected goals come from:
  * scoring and defense: goals for/against per game this season, blended with
    last season early on (this season's weight grows with games played),
  * current form: goals for/against over the last 10 games,
  * the matchup: goals each side scored in their recent head-to-head games,
  * tonight's starting goalies: save percentage vs league average,
  * home ice.
A game is simulated as goals arriving at random through three periods (a
Poisson process at that rate), then 3-on-3 overtime and a shootout if tied.
Goal scorers are drawn by each skater's chance to score (from the parlay
page's slate when it's built, otherwise by recent goals per game).

The 10 simulations are seeded by the game, so the "10 times" are the same for
everyone all day. The replay shown is the most common final score for the team
that won the most of the 10.
"""
import json
import os
import random
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date as Date, timedelta

import deja
import facts
import nhl

RUNS = 10
LEAGUE_GPG = 3.05          # goals per team per game
LEAGUE_SV = 0.900
HOME_EDGE = 1.04
PRIOR_GAMES = 20           # this season's stats get gp / (gp + PRIOR_GAMES) of the weight
H2H_GAMES = 6
_cache = {}                # date -> (built time, result)
_lock = threading.Lock()
TTL = 15 * 60


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------
def _standings(date=None):
    d = nhl._json(f"/standings/{date}" if date else "/standings/now", ttl=1800)
    return {t["teamAbbrev"]["default"]: t for t in d.get("standings") or []}


def _per_game(t, gf, ga, gp):
    n = t.get(gp) or 0
    return ((t.get(gf) or 0) / n, (t.get(ga) or 0) / n, n) if n else (None, None, 0)


def _team_rates(abbr, now, last):
    """(goals for per game, goals against per game, form note) for a team."""
    t, p = now.get(abbr) or {}, last.get(abbr) or {}
    gf, ga, gp = _per_game(t, "goalFor", "goalAgainst", "gamesPlayed")
    pgf, pga, _ = _per_game(p, "goalFor", "goalAgainst", "gamesPlayed")
    pgf, pga = pgf or LEAGUE_GPG, pga or LEAGUE_GPG
    w = gp / (gp + PRIOR_GAMES)
    sgf = w * (gf or 0) + (1 - w) * pgf
    sga = w * (ga or 0) + (1 - w) * pga
    l10f, l10a, l10 = _per_game(t, "l10GoalsFor", "l10GoalsAgainst", "l10GamesPlayed")
    if l10:   # recent form: up to 40% of the weight once 10 games are in
        k = 0.4 * l10 / 10
        sgf = (1 - k) * sgf + k * l10f
        sga = (1 - k) * sga + k * l10a
    rec = f"{t.get('wins', 0)}-{t.get('losses', 0)}-{t.get('otLosses', 0)}" if t else ""
    l10rec = f"{t.get('l10Wins', 0)}-{t.get('l10Losses', 0)}-{t.get('l10OtLosses', 0)}" if l10 else ""
    return sgf, sga, {"record": rec, "last10": l10rec,
                      "gfPerGame": round(sgf, 2), "gaPerGame": round(sga, 2)}


def _h2h(home, away, season, date):
    """Recent finished meetings: [{date, home, away, hs, as}] newest first."""
    out = []
    for s in (season, season - 10001, season - 20002):
        try:
            games = nhl._json(f"/club-schedule-season/{home}/{s}", ttl=6 * 3600).get("games") or []
        except Exception:
            continue
        for g in games:
            teams = {g["homeTeam"]["abbrev"], g["awayTeam"]["abbrev"]}
            if teams == {home, away} and g.get("gameState") in ("OFF", "FINAL") and g["gameDate"] < date:
                out.append({"date": g["gameDate"], "home": g["homeTeam"]["abbrev"], "away": g["awayTeam"]["abbrev"],
                            "hs": g["homeTeam"].get("score") or 0, "as": g["awayTeam"].get("score") or 0,
                            "ot": (g.get("gameOutcome") or {}).get("lastPeriodType") in ("OT", "SO")})
    out.sort(key=lambda g: g["date"], reverse=True)
    return out[:H2H_GAMES]


def _goalie(starter, season, as_of=False):
    """{name, svPct, status} for a starter, using this season blended with last season
    (only last season when rebuilding a past day, so later games can't leak in)."""
    if not starter:
        return None
    try:
        rows = [r for r in nhl.landing(starter["id"]).get("seasonTotals") or []
                if r.get("leagueAbbrev") == "NHL" and r.get("gameTypeId") == 2]
    except Exception:
        rows = []
    shots = saves = 0.0
    for r in rows:
        if r.get("season") not in ((season - 10001,) if as_of else (season, season - 10001)):
            continue
        sa = r.get("shotsAgainst") or 0
        sv = r.get("savePctg") if r.get("savePctg") is not None else r.get("savePercentage")
        if sa and sv:
            shots += sa
            saves += sa * sv
    sv = (saves + 400 * LEAGUE_SV) / (shots + 400)    # 400 shots of league-average prior
    return {"name": starter["name"], "status": starter.get("status", ""), "svPct": round(sv, 3)}


# ---------------------------------------------------------------------------
# the simulation
# ---------------------------------------------------------------------------
def _poisson_times(rng, rate_per_min, start, minutes):
    t, out = start, []
    while rate_per_min > 0:
        t += rng.expovariate(rate_per_min)
        if t >= start + minutes:
            return out
        out.append(t)


def _pick(rng, roster):
    total = sum(w for _, w in roster)
    r = rng.random() * total
    for name, w in roster:
        r -= w
        if r <= 0:
            return name
    return roster[-1][0] if roster else "Unknown"


def play(rng, lam, rosters, home, away):
    """One game: {"events": [...], "score": {home, away}, "end": REG|OT|SO, "shots": {...}}."""
    events = []
    for side, team in (("home", home), ("away", away)):
        for t in _poisson_times(rng, lam[side] / 60.0, 0.0, 60.0):
            events.append({"t": round(t, 2), "team": team, "side": side, "scorer": _pick(rng, rosters[side])})
    events.sort(key=lambda e: e["t"])
    hs = sum(1 for e in events if e["side"] == "home")
    as_ = len(events) - hs
    end = "REG"
    if hs == as_:
        # 3-on-3 overtime: scoring roughly doubles; first goal wins
        rate = 2.0 * (lam["home"] + lam["away"]) / 60.0
        t = rng.expovariate(rate) if rate else 99
        if t < 5:
            side = "home" if rng.random() < lam["home"] / (lam["home"] + lam["away"]) else "away"
            events.append({"t": round(60 + t, 2), "team": home if side == "home" else away, "side": side,
                           "scorer": _pick(rng, rosters[side])})
            end = "OT"
        else:
            side = "home" if rng.random() < 0.5 + 0.1 * (lam["home"] - lam["away"]) / max(lam["home"], lam["away"]) else "away"
            events.append({"t": 65.0, "team": home if side == "home" else away, "side": side, "scorer": "Shootout winner",
                           "shootout": True})
            end = "SO"
        if side == "home":
            hs += 1
        else:
            as_ += 1
    # shots: about 29 a game for an average team, more for teams expected to score more
    shots = {s: max(len([e for e in events if e["side"] == s and not e.get("shootout")]) + 8,
                    int(round(rng.gauss(29 * (lam[s] / LEAGUE_GPG) ** 0.5, 4)))) for s in ("home", "away")}
    return {"events": events, "score": {"home": hs, "away": as_}, "end": end, "shots": shots}


def _rosters(game, slate_players):
    """{"home"|"away": [(name, weight)]} for goal scorers."""
    out = {}
    for side in ("home", "away"):
        abbr = game[side]
        ps = [p for p in slate_players if p["team"] == abbr
              and (p.get("lineup") or {}).get("status") in ("confirmed", "projected")]
        if not ps:
            ps = [p for p in slate_players if p["team"] == abbr]
        out[side] = [(p["name"], max(0.01, p["prob"])) for p in ps] or [(f"{abbr} skater", 1.0)]
    return out


def simulate_game(game, ctx):
    """Inputs + 10 seeded simulations + the one to show."""
    home, away = game["home"], game["away"]
    hgf, hga, hinfo = _team_rates(home, ctx["now"], ctx["last"])
    agf, aga, ainfo = _team_rates(away, ctx["now"], ctx["last"])
    lam = {"home": LEAGUE_GPG * (hgf / LEAGUE_GPG) * (aga / LEAGUE_GPG) * HOME_EDGE,
           "away": LEAGUE_GPG * (agf / LEAGUE_GPG) * (hga / LEAGUE_GPG) / HOME_EDGE}
    h2h = _h2h(home, away, ctx["season"], ctx["date"])
    if h2h:   # matchup history: lean up to 15% toward what each side scored in these meetings
        for side, team in (("home", home), ("away", away)):
            got = sum(g["hs"] if g["home"] == team else g["as"] for g in h2h) / len(h2h)
            k = 0.15 * len(h2h) / H2H_GAMES
            lam[side] = (1 - k) * lam[side] + k * got
    goalies = {"home": _goalie(ctx["starters"].get(home), ctx["season"], ctx.get("asOf")),
               "away": _goalie(ctx["starters"].get(away), ctx["season"], ctx.get("asOf"))}
    for side, opp in (("home", "away"), ("away", "home")):
        g = goalies[opp]
        if g:   # a hot goalie suppresses goals; a leaky one gives them up
            lam[side] *= min(1.3, max(0.75, (1 - g["svPct"]) / (1 - LEAGUE_SV)))
    lam = {k: round(v, 3) for k, v in lam.items()}

    rosters = _rosters(game, ctx["players"])
    rng = random.Random(f"{ctx['date']}|{game['id']}")
    runs = [play(rng, lam, rosters, home, away) for _ in range(RUNS)]
    wins = Counter("home" if r["score"]["home"] > r["score"]["away"] else "away" for r in runs)
    winner = "home" if wins["home"] > wins["away"] or (wins["home"] == wins["away"] and lam["home"] >= lam["away"]) else "away"
    mine = [r for r in runs if (r["score"]["home"] > r["score"]["away"]) == (winner == "home")]
    common = Counter((r["score"]["home"], r["score"]["away"], r["end"]) for r in mine).most_common(1)[0][0]
    shown = next(r for r in mine if (r["score"]["home"], r["score"]["away"], r["end"]) == common)
    return {"id": game["id"], "home": home, "away": away, "homeName": game.get("homeName"), "awayName": game.get("awayName"),
            "start": game.get("start"), "venue": game.get("venue"), "expectedGoals": lam,
            "teams": {"home": hinfo, "away": ainfo}, "goalies": goalies,
            "h2h": h2h, "wins": {"home": wins["home"], "away": wins["away"]},
            "runs": [{"home": r["score"]["home"], "away": r["score"]["away"], "end": r["end"]} for r in runs],
            "shown": shown}


def build(date, slate_players, as_of=False):
    """Simulations for every game on `date`. as_of=True rebuilds a past day from what was
    known that morning: standings through the day before, head-to-head games before it,
    the goalies who actually started, and their save % from last season only."""
    games_raw = [g for g in nhl.schedule(date) if g.get("gameType") in (1, 2, 3)]
    if not games_raw:
        return {"date": date, "games": []}
    season = games_raw[0].get("season")
    games = [{"id": g["id"], "home": g["homeTeam"]["abbrev"], "away": g["awayTeam"]["abbrev"],
              "homeName": f"{facts._d(g['homeTeam'].get('placeName'))} {facts._d(g['homeTeam'].get('commonName'))}",
              "awayName": f"{facts._d(g['awayTeam'].get('placeName'))} {facts._d(g['awayTeam'].get('commonName'))}",
              "start": g.get("startTimeUTC"), "venue": facts._d(g.get("venue"))} for g in games_raw]
    try:
        last = _standings(f"{int(str(season)[:4])}-04-15")   # last season, near its end
    except Exception:
        last = {}
    try:
        now = _standings((Date.fromisoformat(date) - timedelta(days=1)).isoformat() if as_of else None)
    except Exception:
        now = {}
    teams = {g[s] for g in games for s in ("home", "away")}
    starters = _actual_starters(games) if as_of else deja.starters(date, teams)
    if not slate_players:   # no parlay slate yet: weight scorers by their recent goals per game
        slate_players = _fallback_players(teams, season)
    ctx = {"date": date, "season": season, "now": now, "last": last, "starters": starters,
           "players": slate_players, "asOf": as_of}
    with ThreadPoolExecutor(6) as ex:
        sims = list(ex.map(lambda g: simulate_game(g, ctx), games))
    return {"date": date, "games": sims, "builtAt": int(time.time())}


def _actual_starters(games):
    """The goalies who really started a finished game: {abbr: {id, name, status}}."""
    out = {}
    for g in games:
        try:
            b = nhl.boxscore(g["id"])
        except Exception:
            continue
        for side in ("awayTeam", "homeTeam"):
            for gl in ((b.get("playerByGameStats") or {}).get(side) or {}).get("goalies") or []:
                if gl.get("starter"):
                    out[(b.get(side) or {}).get("abbrev")] = {"id": gl["playerId"], "status": "Confirmed",
                                                              "name": facts._d(gl.get("name"))}
    return out


def _fallback_players(teams, season):
    out = []
    for abbr in teams:
        try:
            roster = nhl.roster(abbr)
        except Exception:
            continue
        for r in roster:
            out.append({"team": abbr, "name": f"{facts._d(r.get('firstName'))} {facts._d(r.get('lastName'))}",
                        "prob": 0.18 if r.get("positionCode") != "D" else 0.07, "lineup": {}})
    return out


def get(date, slate_players):
    """The day's simulations: the locked ones when the day has them (so the replay is the
    prediction the record grades), otherwise built now, and today's get locked."""
    locked = load_locked(date)
    if locked:
        return dict(locked, record=summary())
    key = (date, bool(slate_players))    # rebuild once the parlay slate (real scorer odds) is ready
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < TTL:
            return dict(hit[1], record=summary())
    res = build(date, slate_players)
    if date == nhl.today_et().isoformat() and res.get("games") and slate_players:
        lock(date, res)
    with _lock:
        _cache[key] = (time.time(), res)
    return dict(res, record=summary())


# ---------------------------------------------------------------------------
# the simulator's own record: did the team that won most of the 10 sims win?
# ---------------------------------------------------------------------------
DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sims")
START = "2026-10-23"      # the simulator's record starts with its first real (locked) predictions
CONFIDENT = 7      # "confident" picks: the winner won at least 7 of the 10 sims


def _path(date):
    return os.path.join(DIR, f"{date}.json")


def load_locked(date):
    try:
        with open(_path(date)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def lock(date, res, rebuilt=False):
    """Save a day's simulations (the morning prediction) unless they're already locked."""
    if os.path.exists(_path(date)):
        return
    os.makedirs(DIR, exist_ok=True)
    out = dict(res, rebuilt=rebuilt)
    for g in out["games"]:
        g["pick"] = g["home"] if g["wins"]["home"] >= g["wins"]["away"] else g["away"]
    tmp = _path(date) + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    os.replace(tmp, _path(date))


def grade(today=None):
    """Fill in the real result for every locked game that has finished."""
    today = (today or nhl.today_et()).isoformat()
    for name in sorted(os.listdir(DIR)) if os.path.isdir(DIR) else []:
        date = name[:-5]
        if not name.endswith(".json") or date >= today:
            continue
        day = load_locked(date)
        if not day or all("result" in g for g in day["games"]):
            continue
        changed = False
        for g in day["games"]:
            if "result" in g:
                continue
            try:
                b = nhl.boxscore(g["id"])
            except Exception:
                continue
            if b.get("gameScheduleState") in ("PPD", "CNCL"):
                g["result"] = {"status": "void"}
            elif b.get("gameState") in ("OFF", "FINAL"):
                hs, as_ = (b.get("homeTeam") or {}).get("score"), (b.get("awayTeam") or {}).get("score")
                won = g["home"] if hs > as_ else g["away"]
                end = (b.get("gameOutcome") or {}).get("lastPeriodType") or "REG"
                g["result"] = {"status": "W" if won == g["pick"] else "L", "home": hs, "away": as_, "end": end,
                               "exact": hs == g["shown"]["score"]["home"] and as_ == g["shown"]["score"]["away"]}
            else:
                continue
            changed = True
        if changed:
            with open(_path(date), "w") as fh:
                json.dump(day, fh, separators=(",", ":"))


def summary():
    """Overall W-L for picking winners, confident picks, exact scores, and each day."""
    tot = {"W": 0, "L": 0}
    conf = {"W": 0, "L": 0}
    close = {"W": 0, "L": 0}
    exact = 0
    days = []
    if os.path.isdir(DIR):
        for name in sorted(os.listdir(DIR), reverse=True):
            if not name.endswith(".json"):
                continue
            day = load_locked(name[:-5])
            if not day:
                continue
            games = []
            for g in day["games"]:
                r = g.get("result")
                if not r or r["status"] not in ("W", "L"):
                    continue
                tot[r["status"]] += 1
                (conf if max(g["wins"].values()) >= CONFIDENT else close)[r["status"]] += 1
                exact += bool(r.get("exact"))
                games.append({"away": g["away"], "home": g["home"], "pick": g["pick"], "wins": g["wins"],
                              "shown": [g["shown"]["score"]["away"], g["shown"]["score"]["home"], g["shown"]["end"]],
                              "real": [r["away"], r["home"], r["end"]], "status": r["status"], "exact": r.get("exact")})
            if games:
                days.append({"date": day["date"], "rebuilt": day.get("rebuilt", False), "games": games})
    return {"start": START, "total": tot, "confident": conf, "close": close, "exact": exact,
            "confidentAt": CONFIDENT, "days": days}
