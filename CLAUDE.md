# Chayka's Evil Parlays

NHL anytime-goal parlays built on random, real facts (birthdays, grudges, news headlines).
Python standard library only (no Node): `python3 server.py` → http://localhost:8000.

## Workflow

- After finishing and testing a change, commit it with a descriptive message and push to
  `main` without asking. Pushing to GitHub (`ryanmcivor-byte/nhlparlay`) makes Render redeploy
  the live site.
- Only push work that's complete and tested. Ask first before force-pushing or rewriting history.

## Rules the site must keep

- Summon picks one skater per game, and only players confirmed or projected to be in
  tonight's lineup.
- Facts must be real: computed from NHL data or taken from a linked news headline. Never invent
  personal details about players.
