# Chayka's Evil Parlays

NHL anytime-goal parlays built on random, real facts (birthdays, grudges, news headlines).
Python standard library only (no Node): `python3 server.py` → http://localhost:8000.

## Workflow

- After finishing and testing a change, commit it with a descriptive message and push to
  `main` without asking. Pushing to GitHub (`ryanmcivor-byte/nhlparlay`) makes Render redeploy
  the live site.
- Only push work that's complete and tested. Ask first before force-pushing or rewriting history.
- A GitHub Action (`.github/workflows/daily.yml`, 9 AM ET) runs `daily.py` and commits `data/`
  (locked picks + W-L results) every morning, so always `git pull --rebase` before pushing.
  Never hand-edit or delete files in `data/picks/`: they are the locked picks the record grades.
- When `public/app.js` or `public/style.css` changes, bump the `?v=` number on both in
  `public/index.html`, so browsers never pair a cached old script with the new page (that
  crashes the page and nothing loads).

## Rules the site must keep

- Summon picks one skater per game, and only players confirmed or projected to be in
  tonight's lineup.
- Facts must be real: computed from NHL data or taken from a linked news headline. Never invent
  personal details about players.
