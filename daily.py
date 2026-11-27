"""The daily job: run once a morning (GitHub Actions, see .github/workflows/daily.yml).

1. Any past day since launch without locked picks gets them rebuilt as the site
   would have made them that morning (marked "rebuilt" on the record).
2. Every finished day is graded into data/results.json.
3. The Scammer's value model fills in who scored on past mornings' prices
   (data/odds/) and refits itself (data/scammer_model.json).
4. Today's picks are locked into data/picks/<today>.json.
5. The Game Simulations record: finished games are graded and today's
   simulations are locked (data/sims/). It counts real predictions only.

The workflow commits data/ back to the repo, which redeploys the live site.

    python3 daily.py            # normal run
    python3 daily.py --no-today # backfill and grade only
"""
import json
import os
import sys
import time
import urllib.request
from datetime import timedelta

import nhl
import picks
import record
import scammer
import sim
import slate

LIVE_URL = os.environ.get("LIVE_URL", "https://chaykas-evil-parlays.onrender.com")


def live_picks(date, wait=300):
    """The picks the live site has already shown for `date` ({vibe: legs}, or {}).

    After picks.FALLBACK_HOUR the site locks the day's picks itself (this job often
    runs hours late), so the record must keep those rather than build new ones mid-day.
    A sleeping free-tier site wakes on the first request and locks once its slate is
    built, so keep asking for up to `wait` seconds."""
    if nhl.now_et().hour < picks.FALLBACK_HOUR:
        return {}
    deadline = time.time() + wait
    while True:
        try:
            req = urllib.request.Request(f"{LIVE_URL}/api/picks?date={date}",
                                         headers={"User-Agent": "evil-parlays-daily"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                day = json.loads(resp.read()).get("picks") or {}
            if isinstance(day, dict) and all(isinstance(v, list) for v in day.values()) and day:
                return day
        except Exception as e:
            print(f"live site not answering yet ({e})", flush=True)
        if time.time() > deadline:
            return {}
        time.sleep(20)


def main(lock_today=True):
    today = nhl.today_et()
    yesterday = (today - timedelta(days=1)).isoformat()

    for d in record._dates(record.START, yesterday):
        if picks.saved(d):
            continue
        t = time.time()
        s = slate.build(d, {}, as_of=True)
        day = picks.make_all(s) if s.get("players") else {}
        if day:
            picks.lock(d, day)
            record.mark_rebuilt(d)
        print(f"rebuilt {d}: {len(s.get('games', []))} games, "
              f"{sum(len(v) for v in day.values())} legs ({time.time() - t:.0f}s)", flush=True)

    results = record.update(today)
    for d in sorted(results)[-3:]:
        print(d, {v: r["result"] for v, r in results[d]["vibes"].items()}, flush=True)

    # the value model learns: past mornings' prices + who scored -> refit
    scammer.backfill(record.START, yesterday, lambda d: slate.build(d, {}, as_of=True))
    scammer.grade_snapshots(today)
    m = scammer.train()
    print(f"value model: {m['n']} bets over {m['days']} days, weights {m['weights']}, "
          f"backtest {m['backtest']}", flush=True)

    today_slate = []

    def slate_today():
        if not today_slate:
            today_slate.append(slate.build(today.isoformat(), {}))
        return today_slate[0]

    existing = picks.saved(today.isoformat())
    if lock_today and any(v not in existing for v in picks.VIBES):   # new day, or a newly added section
        # keep what people have already seen on the site (it locks at FALLBACK_HOUR)
        shown = dict(live_picks(today.isoformat()), **existing)
        if shown != existing:
            existing = shown
            picks.lock(today.isoformat(), existing)
            print(f"adopted the live site's picks for {today}: {', '.join(sorted(existing))}", flush=True)
    if lock_today and any(v not in existing for v in picks.VIBES):
        s = slate_today()
        day = picks.make_all(s, existing) if s.get("players") else {}
        if day and day != existing:
            picks.lock(today.isoformat(), day)
            print(f"locked {today}: " + ", ".join(f"{v}={len(l)}" for v, l in day.items()), flush=True)
        else:
            print(f"no picks locked for {today} (no games, or no lineups known yet)", flush=True)

    # the simulator's record: only real morning predictions count (no rebuilt days)
    sim.grade(today)
    if lock_today and not sim.load_locked(today.isoformat()):
        res = sim.build(today.isoformat(), slate_today().get("players"))
        if res.get("games"):
            sim.lock(today.isoformat(), res)
    st = sim.summary()
    print(f"simulator record: {st['total']['W']}-{st['total']['L']}, confident {st['confident']}, "
          f"exact scores {st['exact']}", flush=True)

    t = record.summary()["total"]
    print(f"record: {t['W']}-{t['L']}" + (f" ({t['P']} push)" if t["P"] else ""), flush=True)


if __name__ == "__main__":
    main(lock_today="--no-today" not in sys.argv)
