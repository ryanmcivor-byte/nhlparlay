"""Build a day's slate of chaos facts in the background.

get_slate(date) never blocks. The first build of a day answers
{"status": "building", "progress": …} until it finishes; after that the last
good slate is always served, and refreshes (every TTL) run in the background
so visitors never wait on a rebuild.
"""
import copy
import threading
import time
import traceback
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import facts
import nhl
import picks

TTL = 10 * 60      # lineups firm up near puck drop, so refresh often
_slates = {}      # date -> {"data": last good slate or None, "at": built time, "build": running build or None}
_lock = threading.Lock()


def get_slate(date):
    with _lock:
        e = _slates.setdefault(date, {"data": None, "at": 0, "build": None, "error": None})
        stale = time.time() - e["at"] >= TTL
        if stale and e["build"] is None:
            e["build"] = {"progress": 0.0, "step": "Waking the goblins"}
            threading.Thread(target=_build, args=(date, e), daemon=True).start()
        data, build, error = e["data"], e["build"], e["error"]
    if data is not None:
        if not data.get("players"):
            return data
        day, note = picks.get_picks(data)
        return dict(data, picks=day, picksNote=note)
    if build is None and error:
        return {"status": "error", "error": error}
    return {"status": "building", "progress": round(build["progress"], 2), "step": build["step"]}


def _name(first, last):
    return f"{facts._d(first)} {facts._d(last)}".strip()


def _build(date, entry):
    try:
        data = build(date, entry["build"])
        with _lock:
            entry["data"], entry["error"] = data, None
    except Exception as e:  # keep the server alive; keep serving the last good slate
        traceback.print_exc()
        with _lock:
            entry["error"] = str(e)
    with _lock:
        entry["at"] = time.time()   # a failed build waits a TTL too, so we don't hammer the APIs
        entry["build"] = None


def build(date, state, as_of=False):
    """The day's slate. With as_of=True (a past day) it is rebuilt as it stood that
    morning: the players who actually dressed, career numbers rolled back to before
    the date, and only news published before it."""
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

    if as_of:
        players = _dressed_players(games, team_words, date, season, state)
    else:
        players = _roster_players(games, team_name, team_words, state)

    # revenge games: opponent is a former NHL team → count meetings since he left
    state.update(progress=0.72, step="Digging up old grudges")
    ctx_rev = {}
    cands = [p for p in players if _played_for(p, team_name.get(p["opp"]))]
    with ThreadPoolExecutor(8) as ex:
        for p, f in zip(cands, ex.map(lambda p: _safe(_revenge)(p, team_name, season, date if as_of else None),
                                      cands)):
            if f:
                ctx_rev[p["id"]] = f

    # news: two searches per team (general + personal-life keywords)
    state.update(progress=0.8, step="Reading the gossip columns")
    queries = []
    for abbr, full in team_name.items():
        queries.append(f'"{full}"')
        queries.append(f'"{full}" (wife OR wedding OR engaged OR baby OR divorce OR girlfriend '
                       f'OR suspended OR fined OR controversy)')
        queries.append(f'"{full}" ("passed away" OR died OR funeral OR grandfather OR grandmother '
                       f'OR tribute OR mourning)')
    items = []
    with ThreadPoolExecutor(6) as ex:
        for res in ex.map(lambda q: _safe(nhl.headlines)(q, 10, 100, day if as_of else None), queries):
            items.extend(res or [])
    news = facts.news_facts(items, players)

    state.update(progress=0.88, step="Checking who's actually dressing")
    lineups = ({p["id"]: {"status": "confirmed", "label": "Dressed", "detail": "Official NHL box score"}
                for p in players} if as_of else _lineups(games, players))

    state.update(progress=0.92, step="Brewing chaos")
    slate_f = facts.slate_facts(players, day)
    out_players = []
    for p in players:
        ctx = {"date": day, "season": season, "homeAbbr": p["homeAbbr"], "teamName": team_name,
               "revenge": ctx_rev, "news": news, "slateFacts": slate_f}
        fs = facts.player_facts(p, ctx)   # may be empty: favourites still need him
        fs.sort(key=lambda f: -f["chaos"])
        out_players.append({
            "id": p["id"], "name": p["name"], "team": p["team"], "opp": p["opp"], "home": p["home"],
            "gameId": p["gameId"], "pos": p["pos"], "num": p["num"], "headshot": p["headshot"],
            "prob": facts.goal_prob(p["ld"], season), "facts": fs, "lineup": lineups[p["id"]],
            "statLine": facts.stat_line(p["ld"], season),
            "chaos": sum(f["chaos"] for f in fs[:3]),
        })
    out_players.sort(key=lambda p: -p["chaos"])
    return {"status": "ready", "date": date, "season": season, "games": games, "players": out_players,
            "newsCount": sum(len(v) for v in news.values()), "builtAt": int(time.time())}


def _roster_players(games, team_name, team_words, state):
    """Today's skaters: each team's current roster, with landing pages."""
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
    state.update(progress=0.1, step="Reading everyone's diaries")
    return _with_landings(entries, state)


def _dressed_players(games, team_words, date, season, state):
    """A past day's skaters: whoever dressed (box score), with landing pages rolled
    back to that morning."""
    state.update(progress=0.05, step="Reading the box scores")
    with ThreadPoolExecutor(8) as ex:
        boxes = dict(zip([g["id"] for g in games], ex.map(_safe(nhl.boxscore), [g["id"] for g in games])))
    entries = []
    for g in games:
        pbg = (boxes.get(g["id"]) or {}).get("playerByGameStats") or {}
        for key, side, opp, home in (("awayTeam", "away", "home", False), ("homeTeam", "home", "away", True)):
            a = g[side]
            for grp in ("forwards", "defense"):
                for r in (pbg.get(key) or {}).get(grp) or []:
                    entries.append({"id": r["playerId"], "team": a, "opp": g[opp], "home": home,
                                    "gameId": g["id"], "homeAbbr": g["home"], "teamWords": team_words[a],
                                    "pos": r.get("position"), "num": r.get("sweaterNumber")})
    state.update(progress=0.1, step="Reading everyone's diaries")
    players = _with_landings(entries, state)
    with ThreadPoolExecutor(16) as ex:
        lds = list(ex.map(lambda p: _safe(_rewind)(p["ld"], p["id"], date, season), players))
    out = []
    for p, ld in zip(players, lds):
        if ld:
            p.update(ld=ld, name=_name(ld.get("firstName"), ld.get("lastName")),
                     last=facts._d(ld.get("lastName")), headshot=ld.get("headshot"))
            out.append(p)
    return out


def _with_landings(entries, state):
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
    return players


def _rewind(ld, pid, date, season):
    """A copy of a landing page as it stood on the morning of `date`: this season's
    games on or after the date removed from career totals, the season row and
    last5Games."""
    ld = copy.deepcopy(ld)
    log = nhl.game_log(pid, season)
    after = [g for g in log if g["gameDate"] >= date]
    rs = (ld.get("careerTotals") or {}).get("regularSeason")
    if rs and after:
        rs["gamesPlayed"] = (rs.get("gamesPlayed") or 0) - len(after)
        for k in ("goals", "assists", "points"):
            rs[k] = (rs.get(k) or 0) - sum(g.get(k) or 0 for g in after)
    rows = [s for s in ld.get("seasonTotals") or [] if s.get("leagueAbbrev") == "NHL"
            and s.get("gameTypeId") == 2 and s.get("season") == season]
    if rows and after:
        row = rows[-1]
        row["gamesPlayed"] = (row.get("gamesPlayed") or 0) - len(after)
        for k in ("goals", "assists", "points"):
            row[k] = (row.get(k) or 0) - sum(g.get(k) or 0 for g in after)
        if row["gamesPlayed"] <= 0:
            ld["seasonTotals"] = [s for s in ld["seasonTotals"] if s is not row]
    before = [g for g in log + nhl.game_log(pid, season - 10001) if g["gameDate"] < date]
    before.sort(key=lambda g: g["gameDate"], reverse=True)
    ld["last5Games"] = [{"gameDate": g["gameDate"], "goals": g.get("goals"), "gameTypeId": 2,
                         "opponentAbbrev": g.get("opponentAbbrev"), "teamAbbrev": g.get("teamAbbrev")}
                        for g in before[:5]]
    return ld


def _safe(fn):
    def run(*a):
        try:
            return fn(*a)
        except Exception:
            return None
    return run


def _played_for(p, full):
    return bool(full) and any(facts._d(s.get("teamName")) == full and s["season"] for s in facts.nhl_seasons(p["ld"]))


def _revenge(p, team_name, season, before=None):
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
            if before and gm["gameDate"] >= before:
                continue
            if gm.get("teamAbbrev") == opp and (last_date is None or gm["gameDate"] > last_date):
                last_date = gm["gameDate"]
        s += 10001
    if last_date is None:
        return None
    s = last_season
    while s <= season:
        since += sum(1 for gm in nhl.game_log(p["id"], s)
                     if gm.get("opponentAbbrev") == opp and gm["gameDate"] > last_date
                     and not (before and gm["gameDate"] >= before))
        s += 10001
    if since == 0:
        return facts._fact("revenge", "😈", "FIRST game vs his old team",
                           f"First time facing {opp} since leaving (last game for them: {last_date}). "
                           f"{g} goals in {gp} games in their sweater. Revenge SZN.", 10)
    return facts._fact("revenge", "😤", f"Revenge game vs {opp}",
                       f"Former {opp} player ({g} G in {gp} GP for them, last on {last_date}). "
                       f"Seen them {facts._plural(since, 'time')} since leaving.", 7)


# ---------------------------------------------------------------------------
# lineup status: confirmed (NHL official) > projected (Daily Faceoff) > gtd / out
# ---------------------------------------------------------------------------
LINE_LABEL = {"f1": "1st line", "f2": "2nd line", "f3": "3rd line", "f4": "4th line",
              "d1": "1st pair", "d2": "2nd pair", "d3": "3rd pair"}
OUT_STATUSES = {"out", "injured reserve", "suspension"}


def _norm(name):
    s = unicodedata.normalize("NFKD", name or "")
    return "".join(c for c in s if c.isalpha()).lower()


def _lineups(games, players):
    """{player id: {"status": confirmed|projected|gtd|out|unknown, "label", "detail"}}"""
    safe_dressed, safe_scr = _safe(nhl.dressed), _safe(nhl.scratches)
    teams = sorted({p["team"] for p in players})
    with ThreadPoolExecutor(8) as ex:
        official = dict(zip([g["id"] for g in games], ex.map(lambda g: safe_dressed(g["id"]) or {}, games)))
        scr = dict(zip([g["id"] for g in games], ex.map(lambda g: safe_scr(g["id"]) or set(), games)))
        dfo = dict(zip(teams, ex.map(_safe(nhl.dfo_lineup), teams)))
    inj = {_norm(n): st.lower() for n, st in (_safe(nhl.espn_injuries)() or [])}

    out = {}
    for p in players:
        off = official.get(p["gameId"], {}).get(p["team"])
        if off:
            out[p["id"]] = ({"status": "confirmed", "label": "In tonight's lineup", "detail": "Official NHL lineup"}
                            if p["id"] in off else
                            {"status": "out", "label": "Not dressing", "detail": "Not in the official NHL lineup"})
            continue
        if p["id"] in scr.get(p["gameId"], ()):
            out[p["id"]] = {"status": "out", "label": "Scratched", "detail": "Listed as a scratch by the NHL"}
            continue
        lu = dfo.get(p["team"])
        if not lu:
            out[p["id"]] = {"status": "unknown", "label": "Lineup unknown", "detail": "No projected lineup found"}
            continue
        full, last = _norm(p["name"]), _norm(p["last"])
        mine = [x for x in lu["players"] if _norm(x["name"]) == full
                or (x["num"] == p["num"] and _norm(x["name"]).endswith(last))]
        ev = next((x for x in mine if x["cat"] == "ev" and x["group"] in LINE_LABEL), None)
        pp = next((x for x in mine if x["group"] in ("pp1", "pp2")), None)
        hurt = next((x for x in mine if x["cat"] == "oi" or x["injury"]), None)
        espn = inj.get(full, "")
        if hurt and (hurt.get("injury") or "").lower() == "dtd" and ev:
            hurt = None            # day-to-day but still slotted in the lineup: a game-time call
            espn = espn or "day-to-day"
        if hurt or espn in OUT_STATUSES:
            why = (hurt or {}).get("injury") or espn
            out[p["id"]] = {"status": "out", "label": "Injured / out", "detail": f"Listed as {why.upper() if len(why) <= 3 else why}"}
        elif not ev:
            out[p["id"]] = {"status": "out", "label": "Not in projected lineup",
                            "detail": "Not in Daily Faceoff's projected 18 skaters"}
        elif ev["gtd"] or espn == "day-to-day":
            out[p["id"]] = {"status": "gtd", "label": "Game-time decision",
                            "detail": "Day-to-day" if espn == "day-to-day" else "Flagged as a game-time decision"}
        else:
            label = LINE_LABEL[ev["group"]] + (f" · PP{pp['group'][-1]}" if pp else "")
            out[p["id"]] = {"status": "projected", "label": label,
                            "detail": "Projected lineup (Daily Faceoff), not yet confirmed by the NHL"}
    return out
