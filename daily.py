"""The daily job: run once a morning (GitHub Actions, see .github/workflows/daily.yml).

1. Any past day since launch without locked picks gets them rebuilt as the site
   would have made them that morning (marked "rebuilt" on the record).
2. Every finished day is graded into data/results.json.
3. The Scammer's value model fills in who scored on past mornings' prices
   (data/odds/) and refits itself (data/scammer_model.json).
4. Today's picks are locked into data/picks/<today>.json.

The workflow commits data/ back to the repo, which redeploys the live site.

    python3 daily.py            # normal run
    python3 daily.py --no-today # backfill and grade only
"""
import sys
import time
from datetime import timedelta

import nhl
import picks
import record
import scammer
import slate


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

    existing = picks.saved(today.isoformat())
    if lock_today and any(v not in existing for v in picks.VIBES):   # new day, or a newly added section
        s = slate.build(today.isoformat(), {})
        day = picks.make_all(s, existing) if s.get("players") else {}
        if day and day != existing:
            picks.lock(today.isoformat(), day)
            print(f"locked {today}: " + ", ".join(f"{v}={len(l)}" for v, l in day.items()), flush=True)
        else:
            print(f"no picks locked for {today} (no games, or no lineups known yet)", flush=True)

    t = record.summary()["total"]
    print(f"record: {t['W']}-{t['L']}" + (f" ({t['P']} push)" if t["P"] else ""), flush=True)


if __name__ == "__main__":
    main(lock_today="--no-today" not in sys.argv)
