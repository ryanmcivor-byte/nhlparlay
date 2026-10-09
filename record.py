"""Win-loss record: every day's locked parlays graded from NHL box scores.

A parlay is a Win when every leg scores, a Loss as soon as one leg doesn't.
A leg whose player didn't dress (or whose game was postponed) is void, like a
sportsbook: the parlay is graded on its other legs, and a parlay with every
leg void is a push that doesn't count. Results live in data/results.json.
"""
import json
import os
import threading
from datetime import date as Date, timedelta

import nhl
import picks

START = "2026-09-30"           # the day the site launched
ROOT = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(ROOT, "data", "results.json")
REBUILT = os.path.join(ROOT, "data", "rebuilt.json")
FINAL = ("OFF", "FINAL")

_lock = threading.Lock()


def _read(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def rebuilt_dates():
    return set(_read(REBUILT, []))


def mark_rebuilt(date):
    dates = rebuilt_dates() | {date}
    _write(REBUILT, sorted(dates))


def _player_line(box, pid):
    pbg = box.get("playerByGameStats") or {}
    for side in ("awayTeam", "homeTeam"):
        for grp in ("forwards", "defense", "goalies"):
            for p in (pbg.get(side) or {}).get(grp) or []:
                if p.get("playerId") == pid:
                    return p
    return None


def grade_leg(leg, box):
    """{"result": win|loss|void|pending, "goals": n}"""
    if not box:
        return {"result": "pending", "goals": None}
    if box.get("gameScheduleState") in ("PPD", "CNCL"):
        return {"result": "void", "goals": None, "why": "Game postponed"}
    if box.get("gameState") not in FINAL:
        return {"result": "pending", "goals": None}
    line = _player_line(box, leg["id"])
    if line is None:
        return {"result": "void", "goals": None, "why": "Didn't dress"}
    g = line.get("goals") or 0
    return {"result": "win" if g > 0 else "loss", "goals": g}


def grade_parlay(legs, boxes):
    graded = []
    for leg in legs:
        r = grade_leg(leg, boxes.get(leg["gameId"]))
        graded.append(dict(r, id=leg["id"], name=leg["name"], team=leg["team"], opp=leg["opp"],
                           home=leg["home"], fact=leg.get("fact", {}).get("title", "")))
    res = [g["result"] for g in graded]
    if "loss" in res:
        result = "L"
    elif "pending" in res:
        result = "pending"
    elif "win" in res:
        result = "W"
    else:
        result = "P"   # every leg void
    return {"result": result, "legs": graded}


def _dates(start, end):
    d, end = Date.fromisoformat(start), Date.fromisoformat(end)
    while d <= end:
        yield d.isoformat()
        d += timedelta(days=1)


def update(today=None):
    """Grade every day from START to yesterday that has locked picks and isn't final yet."""
    today = today or nhl.today_et()
    yesterday = (today - timedelta(days=1)).isoformat()
    with _lock:
        results = _read(RESULTS, {})
        rebuilt = rebuilt_dates()
        changed = False
        for d in _dates(START, yesterday):
            day = picks.saved(d)
            if not day:
                continue
            done = results.get(d)
            if done and all(v["result"] != "pending" for v in done["vibes"].values()):
                continue
            boxes = {}
            for gid in {leg["gameId"] for legs in day.values() for leg in legs}:
                try:
                    boxes[gid] = nhl.boxscore(gid)
                except Exception:
                    boxes[gid] = None
            results[d] = {"rebuilt": d in rebuilt,
                          "vibes": {v: grade_parlay(legs, boxes) for v, legs in day.items()}}
            changed = True
        if changed:
            _write(RESULTS, results)
    return results


def summary():
    """Totals overall and per vibe, plus each graded day (newest first)."""
    results = _read(RESULTS, {})
    total = {"W": 0, "L": 0, "P": 0}
    by_vibe = {v: {"W": 0, "L": 0, "P": 0} for v in picks.VIBES}
    days = []
    for d in sorted(results, reverse=True):
        day = results[d]
        for v, r in day["vibes"].items():
            if r["result"] in total:
                total[r["result"]] += 1
                by_vibe.setdefault(v, {"W": 0, "L": 0, "P": 0})[r["result"]] += 1
        days.append({"date": d, "rebuilt": day.get("rebuilt", False), "vibes": day["vibes"]})
    return {"start": START, "total": total, "byVibe": by_vibe, "days": days}


_refreshing = [False]


def refresh_async():
    """Grade in the background (the server calls this; it never blocks a request)."""
    if _refreshing[0]:
        return
    _refreshing[0] = True

    def run():
        try:
            update()
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            _refreshing[0] = False

    threading.Thread(target=run, daemon=True).start()
