# P0 / P1 Status Summary

## P0 — Long-term file/data architecture

Completed.

- `index.html` is now a small shell.
- Date data is split into `data/YYYY-MM-DD.json`.
- `data/manifest.json` lists available dates newest first.
- Browser loads only the selected date via `fetch()`.

## P1 — Operational robustness

Completed.

- Added `scripts/news_check_freshness.py`.
- Added aggregated source health output under local runtime archive.
- Daily digest cron should run freshness gate before summarizing.

## Next

P4 deployment makes the Telegram link phone-accessible. This GitHub Pages repo is the first deployment target.
