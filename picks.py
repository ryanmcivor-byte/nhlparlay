"""Each day's locked parlays: three legs per vibe, the same for everyone.

A day's picks live in data/picks/<date>.json, which is committed to the repo:
the daily job (daily.py, run by GitHub Actions around 9 AM ET) locks them, so
they survive the free host's restarts and are what the W-L record grades.
If the job hasn't produced today's file by FALLBACK_HOUR, the server locks
picks itself from its live slate. The choice is deterministic (seeded by
date + vibe + player).
"""
import hashlib
import json
import os
import threading

import nhl

LEGS = 3
PLAYING = ("confirmed", "projected")
GRUDGE = {"revenge", "hometown", "draft", "newteam", "names", "twins"}
VIBES = ("chaos", "gossip", "grudge", "any", "favourites")
# pick order: the pickiest vibes go first so each gets its best legs; no player
# appears in two vibes' parlays on the same day
ORDER = ("gossip", "grudge", "favourites", "chaos", "any")
DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "picks")
DROP_TIME = "9 AM ET"     # when the daily job runs (see .github/workflows/daily.yml)
FALLBACK_HOUR = 11        # ET hour after which the server locks today's picks itself

_locks = {}
_lock = threading.Lock()


def _keep(vibe, f):
    if vibe == "gossip":
        return f["kind"].startswith("news-")
    if vibe == "grudge":
        return f["kind"] in GRUDGE
    return True


def _u(*parts):
    """Stable pseudo-random number in (0, 1) from the given parts."""
    h = hashlib.sha256("|".join(str(x) for x in parts).encode()).digest()
    return (int.from_bytes(h[:8], "big") + 1) / (2 ** 64 + 2)


def _stat_fact(p):
    lu = (p.get("lineup") or {}).get("label") or ""
    text = f"{round(p['prob'] * 100)}% goal chance tonight" + (f". {p['statLine']}." if p.get("statLine") else ".")
    return {"kind": "stat", "emoji": "📈", "title": f"Statistical favourite · {lu}".rstrip(" ·"), "text": text}


def choose(slate, vibe, taken=()):
    """Pick up to LEGS legs, one per game, for `vibe`, skipping players in `taken`."""
    date = slate["date"]
    best = {}  # gameId -> (key, player, fact)
    for p in slate["players"]:
        if (p.get("lineup") or {}).get("status") not in PLAYING or p["id"] in taken:
            continue
        if vibe == "favourites":
            key, fact = p["prob"], _stat_fact(p)
        else:
            fs = [f for f in p["facts"] if _keep(vibe, f)]
            if not fs:
                continue
            w = sum(f["chaos"] for f in fs)
            w = 1 if vibe == "any" else (w * w if vibe == "chaos" else w)
            key = _u(date, vibe, p["id"]) ** (1.0 / w)   # weighted draw, but repeatable
            fact = max(fs, key=lambda f: (f["chaos"], _u(date, vibe, p["id"], f["title"])))
        if p["gameId"] not in best or key > best[p["gameId"]][0]:
            best[p["gameId"]] = (key, p, fact)
    legs = sorted(best.values(), key=lambda x: -x[0])[:LEGS]
    return [{"id": p["id"], "name": p["name"], "team": p["team"], "opp": p["opp"], "home": p["home"],
             "gameId": p["gameId"], "headshot": p.get("headshot"), "prob": p["prob"], "fact": fact}
            for _, p, fact in legs]


def _path(date):
    return os.path.join(DIR, f"{date}.json")


def _load(date):
    try:
        with open(_path(date)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(date, picks):
    try:
        os.makedirs(DIR, exist_ok=True)
        tmp = _path(date) + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(picks, fh)
        os.replace(tmp, _path(date))
    except OSError:
        pass  # memory still holds the lock; the seeded choice covers a lost file


def make_all(slate, day=None):
    """Fill in every vibe missing from `day` ({vibe: legs}); returns the day dict."""
    day = dict(day or {})
    taken = {leg["id"] for legs in day.values() for leg in legs}
    for vibe in ORDER:
        if not day.get(vibe):
            legs = choose(slate, vibe, taken)
            taken |= {leg["id"] for leg in legs}
            if legs:              # nothing to lock before any lineup is known
                day[vibe] = legs
    return day


def saved(date):
    """The locked picks file for `date`, or {}."""
    return _load(date)


def lock(date, day):
    _save(date, day)
    with _lock:
        _locks[date] = day


def get_picks(slate):
    """({vibe: [legs]}, note) for the slate's date. Legs carry each player's current
    lineup status. The note explains an empty result (picks not dropped yet)."""
    date = slate["date"]
    today = nhl.today_et().isoformat()
    with _lock:
        day = _locks.get(date) or _load(date)
        if day:
            _locks[date] = day
        if date == today and nhl.now_et().hour >= FALLBACK_HOUR:
            filled = make_all(slate, day)    # the daily job didn't run: lock them here
            if filled != day:
                day = _locks[date] = filled
                _save(date, day)
        snapshot = {v: [dict(leg) for leg in legs] for v, legs in day.items()}
    note = ""
    if not snapshot:
        if date > today:
            note = f"Picks for this slate drop on game day around {DROP_TIME}."
        elif date == today:
            note = f"Today's picks drop around {DROP_TIME}. Check back soon."
        else:
            note = "No picks were made for this day."
    lineup = {p["id"]: p.get("lineup") for p in slate["players"]}
    for legs in snapshot.values():
        for leg in legs:
            leg["lineup"] = lineup.get(leg["id"]) or {"status": "unknown", "label": "Lineup unknown"}
    return snapshot, note
