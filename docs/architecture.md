# Architecture

## Current phases

- P0: completed — `index.html` shell + lazy-loaded per-date JSON.
- P1: completed — freshness gate and source health tracking.
- P4: current deployment phase — publish static reader through GitHub Pages.
- P2: next — event clustering across multiple sources.
- P3: next — multi-perspective core-truth digest.
- P5: next — retention and observability.

## Data flow

```text
RSS feeds
  ↓ scripts/news_collect_category.py
archive/news/daily/YYYY-MM-DD/*.jsonl
  ↓ scripts/news_check_freshness.py
freshness report
  ↓ scripts/news_prepare_digest_input.py + Hermes LLM digest
archive/news/digests/YYYY-MM-DD-morning.md
  ↓ scripts/news_build_static_site.py
index.html + data/manifest.json + data/YYYY-MM-DD.json
  ↓ GitHub Pages
public static reader
```

## Operational constraints

- Python standard library only.
- No CDN dependency in the page.
- Static site must stay mobile-friendly.
- Telegram should receive short summaries and a link, not full long-form digest text.
