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


def today_et():
    """NHL game dates are Eastern; approximate ET as UTC-4 (close enough for a date)."""
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


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


def headlines(query, days=10, limit=8):
    """Recent Google News headlines for `query`: [{title, source, link, date}]."""
    q = urllib.parse.quote(f"{query} when:{days}d")
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
