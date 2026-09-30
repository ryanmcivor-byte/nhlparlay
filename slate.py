"""Build a day's slate of chaos facts in the background.

get_slate(date) never blocks: it returns {"status": "building", "progress": …}
until the build finishes, then the full slate (cached ~20 minutes).
"""
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import facts
import nhl

TTL = 20 * 60
_slates = {}      # date -> {"status", "progress", "data", "at"}
_lock = threading.Lock()


def get_slate(date):
    with _lock:
        s = _slates.get(date)
        fresh = s and (s["status"] == "building" or time.time() - s["at"] < TTL)
        if not fresh:
            s = {"status": "building", "progress": 0.0, "step": "Waking the goblins", "at": time.time()}
            _slates[date] = s
            threading.Thread(target=_build, args=(date, s), daemon=True).start()
    if s["status"] == "ready":
        return s["data"]
    if s["status"] == "error":
        return {"status": "error", "error": s.get("error", "build failed")}
    return {"status": "building", "progress": round(s["progress"], 2), "step": s["step"]}


def _name(first, last):
    return f"{facts._d(first)} {facts._d(last)}".strip()


def _build(date, state):
    try:
        state["data"] = build(date, state)
        state["status"] = "ready"
    except Exception as e:  # keep the server alive; the page shows the error
        traceback.print_exc()
        state["status"], state["error"] = "error", str(e)
    state["at"] = time.time()


def build(date, state):
    from datetime import date as Date
    day = Date.fromisoformat(date)
    games_raw = [g for g in nhl.schedule(date) if g.get("gameType") in (1, 2, 3)]
    if not games_raw:
        return {"status": "ready", "date": date, "games": [], "players": []}

    season = games_raw[0].get("season") or (day.year * 10000 + day.year + 1 if day.month >= 7
                                             else (day.year - 1) * 10000 + day.year)
    team_name, team_words, games = {}, {}, []
    for g in games_raw:
        for side in ("awayTeam", "homeTeam"):
            t = g[side]
            full = f"{facts._d(t.get('placeName'))} {facts._d(t.get('commonName'))}"
            team_name[t["abbrev"]] = full
            team_words[t["abbrev"]] = {w.lower() for w in full.replace(".", "").split() if len(w) > 2}
        games.append({"id": g["id"], "away": g["awayTeam"]["abbrev"], "home": g["homeTeam"]["abbrev"],
                      "awayName": team_name[g["awayTeam"]["abbrev"]],
                      "homeName": team_name[g["homeTeam"]["abbrev"]],
                      "start": g.get("startTimeUTC"), "venue": facts._d(g.get("venue")),
                      "state": g.get("gameState"), "type": g.get("gameType")})

    # rosters
    state.update(progress=0.05, step="Stealing rosters")
    entries = []
    with ThreadPoolExecutor(8) as ex:
        rosters = dict(zip(team_name, ex.map(_safe(nhl.roster), team_name)))
    for g in games:
        for side, opp, home in (("away", "home", False), ("home", "away", True)):
            a, o = g[side], g[opp]
            for r in rosters.get(a) or []:
                entries.append({"id": r["id"], "name": _name(r.get("firstName"), r.get("lastName")),
                                "last": facts._d(r.get("lastName")), "team": a, "opp": o, "home": home,
                                "gameId": g["id"], "homeAbbr": g["home"], "teamWords": team_words[a],
                                "headshot": r.get("headshot"), "pos": r.get("positionCode"),
                                "num": r.get("sweaterNumber")})

    # player landing pages (bio, career, last 5)
    state.update(progress=0.1, step="Reading everyone's diaries")
    done = [0]

    def load(e):
        ld = _safe(nhl.landing)(e["id"])
        done[0] += 1
        state["progress"] = 0.1 + 0.6 * done[0] / max(1, len(entries))
        return ld

    with ThreadPoolExecutor(16) as ex:
        lds = list(ex.map(load, entries))
    players = []
    for e, ld in zip(entries, lds):
        if ld:
            e["ld"] = ld
            players.append(e)

    # revenge games: opponent is a former NHL team → count meetings since he left
    state.update(progress=0.72, step="Digging up old grudges")
    ctx_rev = {}
    cands = [p for p in players if _played_for(p, team_name.get(p["opp"]))]
    with ThreadPoolExecutor(8) as ex:
        for p, f in zip(cands, ex.map(lambda p: _safe(_revenge)(p, team_name, season), cands)):
            if f:
                ctx_rev[p["id"]] = f

    # news: two searches per team (general + personal-life keywords)
    state.update(progress=0.8, step="Reading the gossip columns")
    queries = []
    for abbr, full in team_name.items():
        queries.append(f'"{full}"')
        queries.append(f'"{full}" (wife OR wedding OR engaged OR baby OR divorce OR girlfriend '
                       f'OR suspended OR traded OR birthday)')
    items = []
    with ThreadPoolExecutor(6) as ex:
        for res in ex.map(lambda q: _safe(nhl.headlines)(q, 10, 100), queries):
            items.extend(res or [])
    news = facts.news_facts(items, players)

    state.update(progress=0.92, step="Brewing chaos")
    slate_f = facts.slate_facts(players, day)
    out_players = []
    for p in players:
        ctx = {"date": day, "season": season, "homeAbbr": p["homeAbbr"], "teamName": team_name,
               "revenge": ctx_rev, "news": news, "slateFacts": slate_f}
        fs = facts.player_facts(p, ctx)
        if not fs:
            continue
        fs.sort(key=lambda f: -f["chaos"])
        out_players.append({
            "id": p["id"], "name": p["name"], "team": p["team"], "opp": p["opp"], "home": p["home"],
            "gameId": p["gameId"], "pos": p["pos"], "num": p["num"], "headshot": p["headshot"],
            "prob": facts.goal_prob(p["ld"], season), "facts": fs,
            "chaos": sum(f["chaos"] for f in fs[:3]),
        })
    out_players.sort(key=lambda p: -p["chaos"])
    return {"status": "ready", "date": date, "season": season, "games": games, "players": out_players,
            "newsCount": sum(len(v) for v in news.values()), "builtAt": int(time.time())}


def _safe(fn):
    def run(*a):
        try:
            return fn(*a)
        except Exception:
            return None
    return run


def _played_for(p, full):
    return bool(full) and any(facts._d(s.get("teamName")) == full and s["season"] for s in facts.nhl_seasons(p["ld"]))


def _revenge(p, team_name, season):
    opp, full = p["opp"], team_name[p["opp"]]
    rows = facts.nhl_seasons(p["ld"])
    with_opp = [s for s in rows if facts._d(s.get("teamName")) == full]
    last_season = with_opp[-1]["season"]
    gp, g = sum(s.get("gamesPlayed") or 0 for s in with_opp), sum(s.get("goals") or 0 for s in with_opp)
    if last_season == season and facts._d(rows[-1].get("teamName")) == full:
        return None  # still with them? (shouldn't happen: he's playing against them)
    years_gone = (season - last_season) // 10001
    if years_gone > 3:
        return facts._fact("revenge", "😈", f"Old team: {opp}",
                           f"Played {gp} games for {opp} ({g} G) back in the day. Grudges don't expire.", 5)
    # find his last game for them, then any meetings since
    last_date, since = None, 0
    s = last_season
    while s <= season:
        for gm in nhl.game_log(p["id"], s):
            if gm.get("teamAbbrev") == opp and (last_date is None or gm["gameDate"] > last_date):
                last_date = gm["gameDate"]
        s += 10001
    if last_date is None:
        return None
    s = last_season
    while s <= season:
        since += sum(1 for gm in nhl.game_log(p["id"], s)
                     if gm.get("opponentAbbrev") == opp and gm["gameDate"] > last_date)
        s += 10001
    if since == 0:
        return facts._fact("revenge", "😈", "FIRST game vs his old team",
                           f"First time facing {opp} since leaving (last game for them: {last_date}). "
                           f"{g} goals in {gp} games in their sweater. Revenge SZN.", 10)
    return facts._fact("revenge", "😤", f"Revenge game vs {opp}",
                       f"Former {opp} player ({g} G in {gp} GP for them, last on {last_date}). "
                       f"Seen them {facts._plural(since, 'time')} since leaving.", 7)
