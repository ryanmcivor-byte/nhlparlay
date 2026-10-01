"""Today's locked parlays: three legs per vibe per day, the same for everyone.

The first time a day's slate is ready, each vibe's legs are chosen and locked
(in memory and in .picks/<date>.json). After that the same legs come back on
every request. The choice itself is deterministic (seeded by date + vibe +
player), so if the lock file is lost on a server restart the same data gives
the same picks again.
"""
import hashlib
import json
import os
import threading

LEGS = 3
PLAYING = ("confirmed", "projected")
GRUDGE = {"revenge", "hometown", "draft", "newteam", "names", "twins"}
VIBES = ("chaos", "gossip", "grudge", "any", "favourites")
DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".picks")

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


def choose(slate, vibe):
    """Pick up to LEGS legs, one per game, for `vibe`."""
    date = slate["date"]
    best = {}  # gameId -> (key, player, fact)
    for p in slate["players"]:
        if (p.get("lineup") or {}).get("status") not in PLAYING:
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


def get_picks(slate):
    """{vibe: [legs]} for the slate's date, locking any vibe not locked yet.
    Each leg carries the player's current lineup status for display."""
    date = slate["date"]
    with _lock:
        day = _locks.get(date)
        if day is None:
            day = _locks[date] = _load(date)
        changed = False
        for vibe in VIBES:
            if not day.get(vibe):
                legs = choose(slate, vibe)
                if legs:              # nothing to lock before any lineup is known
                    day[vibe] = legs
                    changed = True
        if changed:
            _save(date, day)
        snapshot = {v: [dict(leg) for leg in legs] for v, legs in day.items()}
    lineup = {p["id"]: p.get("lineup") for p in slate["players"]}
    for legs in snapshot.values():
        for leg in legs:
            leg["lineup"] = lineup.get(leg["id"]) or {"status": "unknown", "label": "Lineup unknown"}
    return snapshot
