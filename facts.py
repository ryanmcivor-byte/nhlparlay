"""Chaos facts: the random, true things about tonight that 'justify' a goal pick.

Every fact is computed from NHL data or taken from a real, linked news headline.
Nothing is invented. Each fact is a dict:
    {kind, emoji, title, text, chaos (1-10), link?}
"""
import math
import re
from collections import Counter
from datetime import date as Date, timedelta

# city / state-or-province for each club's home arena (hometown-game facts)
TEAM_CITY = {
    "ANA": ("Anaheim", "CA"), "BOS": ("Boston", "MA"), "BUF": ("Buffalo", "NY"),
    "CAR": ("Raleigh", "NC"), "CBJ": ("Columbus", "OH"), "CGY": ("Calgary", "AB"),
    "CHI": ("Chicago", "IL"), "COL": ("Denver", "CO"), "DAL": ("Dallas", "TX"),
    "DET": ("Detroit", "MI"), "EDM": ("Edmonton", "AB"), "FLA": ("Sunrise", "FL"),
    "LAK": ("Los Angeles", "CA"), "MIN": ("Saint Paul", "MN"), "MTL": ("Montreal", "QC"),
    "NJD": ("Newark", "NJ"), "NSH": ("Nashville", "TN"), "NYI": ("Elmont", "NY"),
    "NYR": ("New York", "NY"), "OTT": ("Ottawa", "ON"), "PHI": ("Philadelphia", "PA"),
    "PIT": ("Pittsburgh", "PA"), "SEA": ("Seattle", "WA"), "SJS": ("San Jose", "CA"),
    "STL": ("St. Louis", "MO"), "TBL": ("Tampa", "FL"), "TOR": ("Toronto", "ON"),
    "UTA": ("Salt Lake City", "UT"), "VAN": ("Vancouver", "BC"), "VGK": ("Las Vegas", "NV"),
    "WPG": ("Winnipeg", "MB"), "WSH": ("Washington", "DC"),
}
CITY_ALIASES = {"Montréal": "Montreal", "St Louis": "St. Louis", "Saint Louis": "St. Louis",
                "Québec": "Quebec"}

# news keyword buckets: (kind, emoji, label, chaos, regex)
NEWS_BUCKETS = [
    ("family", "🕯️", "Playing with a heavy heart", 10,
     r"\b(passed away|passes away|died|dies|death of|funeral|in memory of|in honou?r of|mourn\w*|"
     r"tribute to|bereavement|late (father|mother|dad|mom|grandfather|grandmother|brother|sister)|"
     r"grandfather|grandmother|grandpa|grandma|"
     r"(wife|mother|father|dad|mom|son|daughter|brother|sister|family|fianc\w*)('s)? (\w+ ){0,2}"
     r"(cancer|diagnos\w*|illness|hospitali[sz]ed))\b"),
    ("love", "💔", "Love life in the news", 9,
     r"\b(divorc\w*|split(s)? from|breakup|broke up|wedding|married|marries|engaged|engagement|"
     r"fianc\w*|girlfriend|wife|honeymoon)\b"),
    ("baby", "🍼", "New dad energy", 9,
     r"\b(baby|newborn|birth of|becomes? a (dad|father)|fatherhood|paternity|expecting)\b"),
    ("drama", "🔥", "Drama alert", 8,
     r"\b(suspend\w*|fined|(disciplinary|player safety) hearing|hearing (with|for) (the )?(nhl|player safety)|ejected|controvers\w*|feud|trash[- ]talk|chirp\w*|"
     r"arrest\w*|apolog\w*)\b"),
]
_NEWS_RX = [(k, e, lbl, c, re.compile(rx, re.I)) for k, e, lbl, c, rx in NEWS_BUCKETS]


def _d(x):
    return (x or {}).get("default", "") if isinstance(x, dict) else (x or "")


def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def _ordinal(n):
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _date(s):
    try:
        return Date.fromisoformat(s[:10])
    except (TypeError, ValueError):
        return None


def _age_on(bd, on):
    return on.year - bd.year - ((on.month, on.day) < (bd.month, bd.day))


def _fact(kind, emoji, title, text, chaos, link=None):
    f = {"kind": kind, "emoji": emoji, "title": title, "text": text, "chaos": chaos}
    if link:
        f["link"] = link
    return f


def nhl_seasons(ld):
    """Regular-season NHL seasonTotals rows, oldest first."""
    return [s for s in ld.get("seasonTotals") or []
            if s.get("leagueAbbrev") == "NHL" and s.get("gameTypeId") == 2]


def goal_prob(ld, season):
    """Rough anytime-goal chance from recent NHL goals/GP (weighted, shrunk toward
    a positional baseline). This is a vibe number, not a betting model."""
    rows = nhl_seasons(ld)
    pos = ld.get("position") or "C"
    base = 0.09 if pos == "D" else 0.24          # league-ish goals/GP by position
    w_goals = w_gp = 0.0
    for s in rows:
        age = (season - s["season"]) // 10001
        if age < 0 or age > 2:
            continue
        w = (3, 2, 1)[age]
        w_goals += w * (s.get("goals") or 0)
        w_gp += w * (s.get("gamesPlayed") or 0)
    prior = 20.0
    rate = (w_goals + prior * base * 0.8) / (w_gp + prior)
    return round(1 - math.exp(-rate), 3)


# ---------------------------------------------------------------------------
# per-player facts
# ---------------------------------------------------------------------------
def player_facts(p, ctx):
    """p: {id, name, team, opp, home, ld, rosterEntry}; ctx: slate-wide info."""
    ld, today = p["ld"], ctx["date"]
    out = []
    bd = _date(ld.get("birthDate"))
    rows = nhl_seasons(ld)
    career = (ld.get("careerTotals") or {}).get("regularSeason") or {}
    gp, goals = career.get("gamesPlayed") or 0, career.get("goals") or 0
    num = ld.get("sweaterNumber")

    # --- birthdays --------------------------------------------------------
    if bd:
        age_today = _age_on(bd, today)
        if (bd.month, bd.day) == (today.month, today.day):
            out.append(_fact("birthday", "🎂", "It's his birthday",
                             f"Turns {age_today} today. Birthday goals are basically tradition.", 10))
        if num and num == age_today:
            out.append(_fact("numerology", "🔢", "Number matches his age",
                             f"Wears #{num} and is {age_today} years old. The universe is winking.", 6))

    # --- jersey number numerology ----------------------------------------
    if num and num == today.day:
        out.append(_fact("numerology", "📅", "Jersey number = today's date",
                         f"Wears #{num} on the {_ordinal(today.day)} of the month. Destiny.", 6))

    # --- hometown ---------------------------------------------------------
    city = CITY_ALIASES.get(_d(ld.get("birthCity")), _d(ld.get("birthCity")))
    prov = _d(ld.get("birthStateProvince"))
    arena_city, arena_prov = TEAM_CITY.get(ctx["homeAbbr"], ("", ""))
    own_city, own_prov = TEAM_CITY.get(p["team"], ("", ""))
    if p["home"] or (city and city.lower() == own_city.lower()):
        pass  # he plays home games there all season: not a homecoming
    elif city and arena_city and city.lower() == arena_city.lower():
        out.append(_fact("hometown", "🏠", "Hometown game",
                         f"Born in {city}, playing in {arena_city} tonight. Mom's in the stands.", 9))
    elif prov and arena_prov and prov == arena_prov and prov != own_prov:
        out.append(_fact("hometown", "🗺️", "Home-state/province trip",
                         f"Born in {city}, {prov} — tonight's road game is in his home "
                         f"state/province.", 5))

    # --- revenge / new team ----------------------------------------------
    rv = ctx.get("revenge", {}).get(p["id"])
    if rv:
        out.append(rv)
    if rows:
        cur_season = ctx["season"]
        cur_rows = [s for s in rows if s["season"] == cur_season and _d(s.get("teamName")) == ctx["teamName"][p["team"]]]
        prev = [s for s in rows if s["season"] < cur_season]
        if not cur_rows and prev and _d(prev[-1].get("teamName")) != ctx["teamName"][p["team"]]:
            out.append(_fact("newteam", "🆕", f"First game with the {_d(ld.get('teamCommonName')) or p['team']}",
                             f"Hasn't played for {p['team']} yet — last NHL season was with "
                             f"{_d(prev[-1].get('teamName'))}. Debut goals hit different.", 8))
    elif not rows:
        out.append(_fact("debut", "🐣", "Possible NHL debut",
                         "No NHL regular-season games on his record. If he dresses, it's his debut.", 8))

    # --- drafted by the opponent -----------------------------------------
    dr = ld.get("draftDetails") or {}
    if dr.get("teamAbbrev") == p["opp"]:
        played_for = any(_d(s.get("teamName")) == ctx["teamName"].get(p["opp"]) for s in rows)
        if not played_for:
            out.append(_fact("draft", "🎯", f"{p['opp']} drafted him",
                             f"{p['opp']} took him {_ordinal(dr.get('overallPick') or 0)} overall in "
                             f"{dr.get('year')} and he never played a game for them. Petty goal incoming.", 7))

    # --- milestones --------------------------------------------------------
    for m in (50, 100, 150, 200, 250, 300, 350, 400, 450, 500, 600, 700, 800, 900):
        if goals == m - 1:
            out.append(_fact("milestone", "💯", f"One away from goal #{m}",
                             f"Sitting on {goals} career goals. Number {m} has to happen sometime.", 9))
    pts, ast = career.get("points") or 0, career.get("assists") or 0
    for m in (100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1100, 1200, 1300, 1400, 1500):
        if pts == m - 1:
            out.append(_fact("milestone", "🎯", f"One point from #{m}",
                             f"Sitting on {pts} career points. A goal gets him to {m}.", 8))
        if ast == m - 1:
            out.append(_fact("milestone", "🤝", f"One assist from #{m}",
                             f"Sitting on {ast} career assists — but a goal is way funnier.", 5))
    if gp and (gp + 1) % 100 == 0:
        out.append(_fact("milestone", "🎖️", f"Career game #{gp + 1}",
                         f"Tonight would be his {_ordinal(gp + 1)} NHL game. Celebrate with a goal?", 6))
    if gp and goals == 0:
        out.append(_fact("firstgoal", "🥚", "Still chasing goal #1",
                         f"0 goals in {_plural(gp, 'NHL game')}. The dam has to break eventually.", 7))

    # --- recent form (last 5) --------------------------------------------
    # recent form: only games from the last 30 days count as "recent"
    l5 = [g for g in (ld.get("last5Games") or []) if g.get("gameTypeId") == 2
          and (_date(g.get("gameDate")) or today) >= today - timedelta(days=30)]
    if l5:
        streak = 0
        for g in l5:
            if (g.get("goals") or 0) > 0:
                streak += 1
            else:
                break
        if streak >= 2:
            out.append(_fact("hot", "🔥", f"Goals in {streak} straight",
                             f"Scored in each of his last {streak} games. Ride the heater.", 7))
        vs_opp = [g for g in l5 if g.get("opponentAbbrev") == p["opp"] and (g.get("goals") or 0) > 0]
        if vs_opp:
            out.append(_fact("lastmeet", "🔁", f"Just scored on {p['opp']}",
                             f"Scored vs {p['opp']} on {vs_opp[0]['gameDate']}. Twice in a row?", 6))

    # --- news headlines ---------------------------------------------------
    for f in ctx.get("news", {}).get(p["id"], []):
        out.append(f)

    # --- slate-wide oddities (tallest, only-countryman, birthday twins…) -
    out.extend(ctx.get("slateFacts", {}).get(p["id"], []))
    return out


# ---------------------------------------------------------------------------
# slate-wide facts: comparisons across everyone playing tonight
# ---------------------------------------------------------------------------
def slate_facts(players, date):
    by = {}

    def add(pid, f):
        by.setdefault(pid, []).append(f)

    with_bd = [(p, _date(p["ld"].get("birthDate"))) for p in players]
    with_bd = [(p, b) for p, b in with_bd if b]

    # birthday twins on opposite teams in the same game
    games = {}
    for p, b in with_bd:
        games.setdefault(p["gameId"], []).append((p, b))
    for plist in games.values():
        for i, (p1, b1) in enumerate(plist):
            for p2, b2 in plist[i + 1:]:
                if p1["team"] != p2["team"] and (b1.month, b1.day) == (b2.month, b2.day):
                    for a, bb in ((p1, p2), (p2, p1)):
                        add(a["id"], _fact("twins", "👯", f"Birthday twin with {bb['name']}",
                                           f"Shares a birthday ({b1.strftime('%b %-d')}) with {bb['name']} "
                                           f"of {bb['team']}. Only one twin can score first.", 4))
        # same last name on opposite sides (brothers or pure coincidence)
        by_last = {}
        for p, _ in plist:
            by_last.setdefault(p["last"], []).append(p)
        for last, ps in by_last.items():
            if len({x["team"] for x in ps}) > 1:
                for a in ps:
                    others = ", ".join(f"{x['name']} ({x['team']})" for x in ps if x["team"] != a["team"])
                    add(a["id"], _fact("names", "🪞", "Same name across the ice",
                                       f"Faces {others} tonight. Family feud or wild coincidence — "
                                       f"either way, chaos.", 7))

    return by


# ---------------------------------------------------------------------------
# news → facts
# ---------------------------------------------------------------------------
def news_facts(items, players):
    """Match real headlines to players by full name (or unique last name) and
    bucket them by keyword. Only headlines with a bucket hit become facts."""
    by = {}
    lasts = Counter(p["last"].lower() for p in players)
    seen = set()
    for it in items:
        title = it["title"]
        low = title.lower()
        for p in players:
            full = p["name"].lower()
            last = p["last"].lower()
            hit = full in low or (len(last) >= 5 and lasts[last] == 1
                                  and re.search(r"\b" + re.escape(last) + r"\b", low)
                                  and not re.search(r"\b" + re.escape(p["last"]) + r"\s+[A-Z]", title)
                                  and p["teamWords"] & set(re.findall(r"[a-z]+", low)))
            if not hit or (p["id"], title) in seen:
                continue
            for kind, emoji, label, chaos, rx in _NEWS_RX:
                if rx.search(title):
                    seen.add((p["id"], title))
                    bucket = by.setdefault(p["id"], {})
                    if kind in bucket:          # one headline per bucket; count the rest
                        bucket[kind]["more"] += 1
                        break
                    src = f" ({it['source']})" if it.get("source") else ""
                    f = _fact("news-" + kind, emoji, label, f"“{title}”{src}", chaos, it.get("link"))
                    f["more"] = 0
                    bucket[kind] = f
                    break
    out = {}
    for pid, bucket in by.items():
        fs = []
        for f in bucket.values():
            more = f.pop("more")
            if more:
                f["text"] += " +" + _plural(more, "more story").replace("storys", "stories")
            fs.append(f)
        out[pid] = fs
    return out


def stat_line(ld, season):
    """'1 G in 1 GP this season · 27 G in 76 GP in 2025-26' (regular season, NHL only)."""
    rows = {r["season"]: r for r in nhl_seasons(ld)}
    parts = []
    cur, prev = rows.get(season), rows.get(season - 10001)
    if cur and cur.get("gamesPlayed"):
        parts.append(f"{cur.get('goals') or 0} G in {cur['gamesPlayed']} GP this season")
    if prev and prev.get("gamesPlayed"):
        lbl = f"{str(season - 10001)[:4]}-{str(season)[2:4]}"
        parts.append(f"{prev.get('goals') or 0} G in {prev['gamesPlayed']} GP in {lbl}")
    return " · ".join(parts)
