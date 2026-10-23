"""DEJA VU: skaters who have already lit up tonight's starting goalie.

For each starting goalie (ESPN's probable starters), every regular-season and
playoff game he has played in the last SEASONS_BACK seasons plus this one is
read from the NHL play-by-play: each shot on goal and goal against him (not in
a shootout) is tallied by shooter. The tallies are cached per goalie in
data/goalies/<id>.json and only new games are fetched after that.
"""
import json
import os
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import nhl

SEASONS_BACK = 4
MIN_GOALS = 2
DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "goalies")
_locks = {}
_guard = threading.Lock()


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    return re.sub(r"[^a-z]", "", "".join(c for c in s if not unicodedata.combining(c)).lower())


def season_label(season):
    return f"{str(season)[:4]}-{str(season)[6:]}"


def _game_tally(game_id, goalie_id):
    """{shooter id: [goals, shots on goal]} against this goalie in one game (shootouts excluded)."""
    out = {}
    for p in nhl.play_by_play(game_id).get("plays") or []:
        kind = p.get("typeDescKey")
        if kind not in ("goal", "shot-on-goal"):
            continue
        if (p.get("periodDescriptor") or {}).get("periodType") == "SO":
            continue
        d = p.get("details") or {}
        if d.get("goalieInNetId") != goalie_id:
            continue
        shooter = d.get("scoringPlayerId") if kind == "goal" else d.get("shootingPlayerId")
        if not shooter:
            continue
        row = out.setdefault(str(shooter), [0, 0])
        row[1] += 1                      # a goal is a shot on goal too
        if kind == "goal":
            row[0] += 1
    return out


def _path(goalie_id):
    return os.path.join(DIR, f"{goalie_id}.json")


def history(goalie_id, season, before):
    """{shooter id (int): {"goals", "shots", "games"}} against this goalie in finished
    games before `before` (YYYY-MM-DD), over the last SEASONS_BACK seasons + this one."""
    with _guard:
        lock = _locks.setdefault(goalie_id, threading.Lock())
    with lock:
        try:
            with open(_path(goalie_id)) as fh:
                cache = json.load(fh)
        except (OSError, ValueError):
            cache = {"games": {}}
        games = cache["games"]
        wanted = {}
        for s in range(season - SEASONS_BACK * 10001, season + 1, 10001):
            for gtype in (2, 3):
                try:
                    log = nhl.game_log(goalie_id, s, gtype)
                except Exception:
                    log = []
                for g in log:
                    if g.get("gameDate", "9999") < before:
                        wanted[str(g["gameId"])] = g["gameDate"]
        missing = [gid for gid in wanted if gid not in games]
        if missing:
            def fetch(gid):
                try:
                    return gid, _game_tally(int(gid), goalie_id)
                except Exception:
                    return gid, None
            with ThreadPoolExecutor(12) as ex:
                for gid, tally in ex.map(fetch, missing):
                    if tally is not None:
                        games[gid] = {"date": wanted[gid], "shooters": tally}
            os.makedirs(DIR, exist_ok=True)
            tmp = _path(goalie_id) + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(cache, fh, separators=(",", ":"))
            os.replace(tmp, _path(goalie_id))
    out = {}
    for gid, date in wanted.items():
        g = games.get(gid)
        if not g:
            continue
        for pid, (goals, shots) in g["shooters"].items():
            row = out.setdefault(int(pid), {"goals": 0, "shots": 0, "games": 0})
            row["goals"] += goals
            row["shots"] += shots
            row["games"] += 1
    return out


def starters(date, teams):
    """Tonight's starting goalie for each team: {abbr: {"id", "name", "status"}}."""
    try:
        probable = nhl.espn_starters(date)
    except Exception:
        return {}
    out = {}
    for abbr in teams:
        pr = probable.get(abbr)
        if not pr:
            continue
        try:
            roster = nhl.goalies(abbr)
        except Exception:
            roster = []
        g = next((x for x in roster if _norm(x["name"]) == _norm(pr["name"])), None)
        if g:
            out[abbr] = {"id": g["id"], "name": g["name"], "status": pr["status"]}
    return out


def facts_for(players, games, date, season):
    """{skater id: fact} for skaters with MIN_GOALS+ career goals on the goalie they face tonight."""
    teams = {g[s] for g in games for s in ("home", "away")}
    starting = starters(date, teams)
    hist = {}
    with ThreadPoolExecutor(4) as ex:
        for abbr, h in zip(starting, ex.map(lambda a: history(starting[a]["id"], season, date), starting)):
            hist[abbr] = h
    out = {}
    first = season_label(season - SEASONS_BACK * 10001)
    for p in players:
        g = starting.get(p["opp"])
        row = (hist.get(p["opp"]) or {}).get(p["id"])
        if not g or not row or row["goals"] < MIN_GOALS:
            continue
        pct = row["goals"] / row["shots"] if row["shots"] else 0
        who = f"{g['name']}" + ("" if g["status"].lower() == "confirmed" else " (expected starter)")
        out[p["id"]] = {"kind": "dejavu", "emoji": "🌀", "title": f"Owns {g['name'].split()[-1]}: {row['goals']} goals",
                        "text": f"{row['goals']} goals on {row['shots']} shots against {who} in "
                                f"{row['games']} game{'' if row['games'] == 1 else 's'} since {first} ({pct:.0%} shooting). Deja vu?",
                        "chaos": min(10, 4 + row["goals"]),
                        "goals": row["goals"], "shots": row["shots"], "goalie": g["name"]}
    return out
