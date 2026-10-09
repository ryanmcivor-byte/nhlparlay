"""Tiny NHL + news client (Python stdlib only).

- api-web.nhle.com/v1: schedule, rosters, player landing pages, game logs
- news.google.com RSS: recent headlines per player (real, linked stories only)

Everything is cached in memory with a TTL so a page refresh is instant.
"""
import json
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

WEB = "https://api-web.nhle.com/v1"
NEWS = "https://news.google.com/rss/search"
NHL_UA = "curl/8.4.0"          # api-web 403s Python's default User-Agent
NEWS_UA = "Mozilla/5.0 (Macintosh) ChaykasEvilParlays/1.0"

_cache = {}
_lock = threading.Lock()


def _get(url, ua, ttl, parse):
    now = time.time()
    with _lock:
        hit = _cache.get(url)
        if hit and hit[0] > now:
            return hit[1]
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = parse(resp.read())
    with _lock:
        _cache[url] = (now + ttl, data)
    return data


def _json(path, ttl=600):
    return _get(WEB + path, NHL_UA, ttl, json.loads)


def now_et():
    """Current time in US Eastern (NHL game dates are Eastern)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:  # no tz database: UTC-4 is close enough
        return datetime.now(timezone.utc) - timedelta(hours=4)


def today_et():
    return now_et().date()


def schedule(date):
    """Games on `date` (YYYY-MM-DD)."""
    d = _json(f"/schedule/{date}", ttl=300)
    for day in d.get("gameWeek") or []:
        if day.get("date") == date:
            return day.get("games") or []
    return []


def roster(abbr):
    d = _json(f"/roster/{abbr}/current", ttl=3600)
    out = []
    for grp in ("forwards", "defensemen"):     # goalies don't score (much)
        out.extend(d.get(grp) or [])
    return out


def landing(pid):
    return _json(f"/player/{pid}/landing", ttl=3600)


def game_log(pid, season, gtype=2):
    return (_json(f"/player/{pid}/game-log/{season}/{gtype}", ttl=3600).get("gameLog") or [])


def headlines(query, days=10, limit=8, before=None):
    """Recent Google News headlines for `query`: [{title, source, link, date}].
    With `before` (a date), only stories from the `days` days before it, for
    rebuilding what a past day's slate would have seen."""
    if before is not None:
        window = f"after:{before - timedelta(days=days)} before:{before - timedelta(days=1)}"
    else:
        window = f"when:{days}d"
    q = urllib.parse.quote(f"{query} {window}")
    url = f"{NEWS}?q={q}&hl=en-US&gl=US&ceid=US:en"

    def parse(raw):
        items = []
        for it in ET.fromstring(raw).iter("item"):
            title = (it.findtext("title") or "").strip()
            src = (it.findtext("source") or "").strip()
            if src and title.endswith(" - " + src):
                title = title[: -len(src) - 3]
            items.append({"title": title, "source": src, "link": it.findtext("link") or "",
                          "date": it.findtext("pubDate") or ""})
        return items[:limit]

    return _get(url, NEWS_UA, 3 * 3600, parse)


# ---------------------------------------------------------------------------
# Lineups: who is actually dressing tonight
# ---------------------------------------------------------------------------
DFO = "https://www.dailyfaceoff.com/teams"
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126 Safari/537.36")
DFO_ABBR = {"LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL", "UTAH": "UTA", "MON": "MTL", "VEG": "VGK",
            "NAS": "NSH", "WAS": "WSH"}
_NEXT_RX = None


def _next_data(raw):
    global _NEXT_RX
    import re
    if _NEXT_RX is None:
        _NEXT_RX = re.compile(rb'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
    m = _NEXT_RX.search(raw)
    return json.loads(m.group(1))["props"]["pageProps"] if m else {}


def _dfo_page(slug):
    return _get(f"{DFO}/{slug}/line-combinations", BROWSER_UA, 15 * 60, _next_data)


def dfo_slugs():
    """{NHL abbr: Daily Faceoff team slug}, read from any team page's team list."""
    teams = _dfo_page("toronto-maple-leafs").get("sortedTeams") or []
    return {DFO_ABBR.get(t["shortName"], t["shortName"]): t["slug"] for t in teams if t.get("slug")}


def dfo_lineup(abbr):
    """Daily Faceoff line combinations for a team:
    {updatedAt, players: [{name, num, cat, group, gtd, injury}]} or None."""
    slug = dfo_slugs().get(abbr)
    if not slug:
        return None
    c = _dfo_page(slug).get("combinations") or {}
    if not c.get("players"):
        return None
    ps = [{"name": p.get("name") or "", "num": p.get("jerseyNumber"), "cat": p.get("categoryIdentifier"),
           "group": p.get("groupIdentifier"), "gtd": bool(p.get("gameTimeDecision")),
           "injury": p.get("injuryStatus")} for p in c["players"]]
    return {"updatedAt": c.get("updatedAt"), "players": ps}


def boxscore(game_id):
    return _json(f"/gamecenter/{game_id}/boxscore", ttl=300)


def dressed(game_id):
    """Official game lineup {abbr: set(player ids)} once the NHL posts it, else {}."""
    b = boxscore(game_id)
    pbg = b.get("playerByGameStats") or {}
    out = {}
    for key in ("awayTeam", "homeTeam"):
        side = pbg.get(key) or {}
        ids = {p["playerId"] for grp in ("forwards", "defense") for p in side.get(grp) or []}
        if ids:
            out[(b.get(key) or {}).get("abbrev")] = ids
    return out


def scratches(game_id):
    """Healthy scratches the NHL has posted for tonight: set of player ids."""
    r = _json(f"/gamecenter/{game_id}/right-rail", ttl=600)
    info = r.get("gameInfo") or {}
    return {s.get("id") for k in ("awayTeam", "homeTeam") for s in (info.get(k) or {}).get("scratches") or []}


ESPN_INJ = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries"


def espn_injuries():
    """[(full name, status)] league-wide: 'Out', 'Injured Reserve', 'Day-To-Day'…"""
    d = _get(ESPN_INJ, NHL_UA, 30 * 60, json.loads)
    out = []
    for team in d.get("injuries") or []:
        for i in team.get("injuries") or []:
            out.append(((i.get("athlete") or {}).get("displayName") or "", i.get("status") or ""))
    return out
