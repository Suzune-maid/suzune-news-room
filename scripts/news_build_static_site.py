#!/usr/bin/env python3
"""Build a static news reader shell and per-date data files for Hermes."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
DAILY_DIR = BASE_DIR / "archive/news/daily"
DIGEST_DIR = BASE_DIR / "archive/news/digests"
OUTPUT_DIR = BASE_DIR
OUTPUT_FILE = OUTPUT_DIR / "index.html"
DATA_DIR = OUTPUT_DIR / "data"
MANIFEST_FILE = DATA_DIR / "manifest.json"

CATEGORY_ORDER = ["taiwan", "world", "finance", "tech_ai", "society_life"]
CATEGORY_LABELS = {
    "taiwan": "台灣／公共議題",
    "world": "國際",
    "finance": "財經／市場",
    "tech_ai": "科技／AI",
    "society_life": "社會／生活",
}
CATEGORY_BY_LABEL = {label: key for key, label in CATEGORY_LABELS.items()}
CATEGORY_BY_SECTION = {
    "台灣／公共議題": "taiwan",
    "國際": "world",
    "財經／市場": "finance",
    "科技／AI": "tech_ai",
    "社會／生活": "society_life",
}


def now_local_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def json_text(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL in {path}:{line_no}: {exc}") from exc
            if isinstance(item, dict):
                rows.append(item)
    return rows


def split_source_links(value: str) -> list[str]:
    links: list[str] = []
    for part in re.split(r"\s*(?:｜|\||,|，)\s*", value.strip()):
        part = part.strip()
        if part.startswith("<") and part.endswith(">"):
            part = part[1:-1].strip()
        if part.startswith("http://") or part.startswith("https://"):
            links.append(part)
    return dedupe(links)


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def normalize_title(title: str) -> str:
    title = re.sub(r"^\s*\d+[.、]\s*", "", title)
    title = re.sub(r"[^\w\u4e00-\u9fff]+", "", title.lower())
    return title


def one_sentence(text: str, max_len: int = 92) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return ""
    match = re.search(r"^(.+?[。！？!?])", text)
    if match:
        text = match.group(1)
    if len(text) > max_len:
        return text[: max_len - 1].rstrip() + "…"
    return text


def parse_digest(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    sections: dict[str, list[dict[str, Any]]] = {}
    current_section = ""
    current_item: dict[str, Any] | None = None
    current_field: str | None = None

    def finish_item() -> None:
        nonlocal current_item, current_field
        if current_item:
            current_item["links"] = split_source_links(current_item.get("links_raw", ""))
            current_item.pop("links_raw", None)
            sections.setdefault(current_item["section"], []).append(current_item)
        current_item = None
        current_field = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        section_match = re.match(r"^##\s+(.+?)\s*$", line)
        item_match = re.match(r"^###\s+(.+?)\s*$", line)
        field_match = re.match(r"^-\s*(摘要|為什麼值得注意|來源連結)：\s*(.*)$", line)

        if section_match:
            finish_item()
            current_section = section_match.group(1).strip()
            continue
        if item_match and current_section:
            finish_item()
            title = re.sub(r"^\d+[.、]\s*", "", item_match.group(1).strip())
            current_item = {
                "section": current_section,
                "title": title,
                "summary": "",
                "why": "",
                "links_raw": "",
            }
            continue
        if field_match and current_item:
            label, value = field_match.group(1), field_match.group(2).strip()
            if label == "摘要":
                current_item["summary"] = value
                current_field = "summary"
            elif label == "為什麼值得注意":
                current_item["why"] = value
                current_field = "why"
            elif label == "來源連結":
                current_item["links_raw"] = value
                current_field = "links_raw"
            continue
        if current_item and current_field and line.startswith("  "):
            current_item[current_field] = (
                str(current_item.get(current_field, "")).rstrip() + " " + line.strip()
            ).strip()

    finish_item()
    return {
        "date": path.name.removesuffix("-morning.md"),
        "digest_markdown": text,
        "sections": sections,
    }


def category_from_digest_section(section: str, links: list[str], raw_by_url: dict[str, dict[str, Any]]) -> str:
    if section == "今日最重要 5 件事":
        for link in links:
            raw = raw_by_url.get(link)
            if raw:
                return raw.get("category") or CATEGORY_BY_LABEL.get(raw.get("category_label", ""), "top")
        return "top"
    return CATEGORY_BY_SECTION.get(section, "other")


def make_source_links(urls: list[str], raw_by_url: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for index, url in enumerate(dedupe(urls), 1):
        raw = raw_by_url.get(url, {})
        source = str(raw.get("source") or f"來源 {index}")
        links.append({"url": url, "source": source})
    return links


def discover_dates() -> list[str]:
    dates: set[str] = set()
    if DAILY_DIR.exists():
        for date_dir in DAILY_DIR.iterdir():
            if date_dir.is_dir():
                dates.add(date_dir.name)
    if DIGEST_DIR.exists():
        for path in DIGEST_DIR.glob("*-morning.md"):
            dates.add(path.name.removesuffix("-morning.md"))
    return sorted(dates, reverse=True)


def load_daily_for_date(date: str) -> list[dict[str, Any]]:
    date_dir = DAILY_DIR / date
    rows: list[dict[str, Any]] = []
    if not date_dir.exists():
        return rows
    for category_path in sorted(date_dir.glob("*.jsonl")):
        rows.extend(read_jsonl(category_path))
    rows.sort(key=lambda row: str(row.get("published") or row.get("collected_at") or ""), reverse=True)
    return rows


def load_source_status_for_date(date: str) -> list[dict[str, Any]]:
    date_dir = DAILY_DIR / date
    statuses: list[dict[str, Any]] = []
    if not date_dir.exists():
        return statuses
    for status_path in sorted(date_dir.glob("*.status.json")):
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            status = {"file": status_path.name, "error": "invalid status json"}
        if isinstance(status, dict):
            status.setdefault("file", status_path.name)
            statuses.append(status)
    return statuses


def load_digests() -> dict[str, dict[str, Any]]:
    digests: dict[str, dict[str, Any]] = {}
    if not DIGEST_DIR.exists():
        return digests
    for path in sorted(DIGEST_DIR.glob("*-morning.md")):
        parsed = parse_digest(path)
        digests[parsed["date"]] = parsed
    return digests


def build_date_payload(
    date: str,
    raw_rows: list[dict[str, Any]] | None = None,
    digest: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    if raw_rows is None:
        raw_rows = load_daily_for_date(date)
    if digest is None:
        digest_path = DIGEST_DIR / f"{date}-morning.md"
        digest = parse_digest(digest_path) if digest_path.exists() else None

    raw_by_url = {str(row.get("url")): row for row in raw_rows if row.get("url")}
    used_urls: set[str] = set()
    cards: list[dict[str, Any]] = []
    pinned: list[dict[str, Any]] = []

    def digest_card(item: dict[str, Any], section: str, pinned_item: bool = False) -> dict[str, Any]:
        links = item.get("links") or []
        for link in links:
            used_urls.add(link)
        category = category_from_digest_section(section, links, raw_by_url)
        title = str(item.get("title") or "").strip()
        summary = str(item.get("summary") or "").strip()
        why = str(item.get("why") or "").strip()
        return {
            "id": f"{date}-digest-{len(cards)}-{len(pinned)}",
            "date": date,
            "title": title,
            "summary": one_sentence(summary),
            "detailSummary": summary,
            "why": why,
            "category": category,
            "categoryLabel": CATEGORY_LABELS.get(category, section if category != "top" else "今日最重要"),
            "sourceCount": len(dedupe(links)) or 1,
            "sources": make_source_links(links, raw_by_url),
            "pinned": pinned_item,
            "fromDigest": True,
        }

    if digest:
        sections = digest.get("sections", {})
        for item in sections.get("今日最重要 5 件事", []):
            pinned.append(digest_card(item, "今日最重要 5 件事", True))
        for category_label in CATEGORY_LABELS.values():
            for item in sections.get(category_label, []):
                cards.append(digest_card(item, category_label, False))

    for raw in raw_rows:
        url = str(raw.get("url") or "")
        if url and url in used_urls:
            continue
        category = str(raw.get("category") or CATEGORY_BY_LABEL.get(str(raw.get("category_label") or ""), "other"))
        title = str(raw.get("title") or "").strip()
        summary = str(raw.get("summary") or "").strip()
        cards.append(
            {
                "id": f"{date}-raw-{len(cards)}",
                "date": date,
                "title": title,
                "summary": one_sentence(summary),
                "detailSummary": summary,
                "why": "",
                "category": category,
                "categoryLabel": str(raw.get("category_label") or CATEGORY_LABELS.get(category, "其他")),
                "sourceCount": 1,
                "sources": make_source_links([url] if url else [], raw_by_url),
                "published": str(raw.get("published") or ""),
                "sourceName": str(raw.get("source") or ""),
                "pinned": False,
                "fromDigest": False,
            }
        )

    category_rank = {name: index for index, name in enumerate(CATEGORY_ORDER)}
    cards.sort(key=lambda card: (category_rank.get(card["category"], 99), card["title"]))
    return {
        "date": date,
        "generated_at": generated_at or now_local_iso(),
        "digest_markdown": str((digest or {}).get("digest_markdown") or ""),
        "categoryLabels": CATEGORY_LABELS,
        "categories": [{"key": key, "label": CATEGORY_LABELS[key]} for key in CATEGORY_ORDER],
        "source_status": load_source_status_for_date(date),
        "pinned": pinned,
        "cards": cards,
    }


def build_site_payloads() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    generated_at = now_local_iso()
    dates = discover_dates()
    digests = load_digests()
    date_payloads: dict[str, dict[str, Any]] = {}
    for date in dates:
        date_payloads[date] = build_date_payload(
            date,
            raw_rows=load_daily_for_date(date),
            digest=digests.get(date),
            generated_at=generated_at,
        )
    manifest = {
        "generated_at": generated_at,
        "latest": dates[0] if dates else "",
        "dates": dates,
        "categories": [{"key": key, "label": CATEGORY_LABELS[key]} for key in CATEGORY_ORDER],
    }
    return manifest, date_payloads


def render_html() -> str:
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>鈴音新聞整理室</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f8fbff;
      --panel: #ffffff;
      --panel-soft: #f4f7ff;
      --ink: #243044;
      --muted: #63718a;
      --line: #d9e2fb;
      --line-strong: #c9d4f5;
      --blue: #4d8df7;
      --blue-dark: #2f65c8;
      --violet: #8b73ef;
      --violet-dark: #6f54db;
      --violet-soft: #f1edff;
      --rose-soft: #fff7fb;
      --shadow: 0 16px 36px rgba(84, 99, 152, 0.15);
      --shadow-soft: 0 9px 22px rgba(84, 99, 152, 0.10);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at 12% 0%, rgba(77,141,247,0.18), transparent 24rem),
        radial-gradient(circle at 92% 8%, rgba(139,115,239,0.24), transparent 26rem),
        linear-gradient(180deg, #fbfdff 0%, #f6f8ff 46%, #ffffff 100%);
      color: var(--ink);
      font-size: 16px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans TC", "PingFang TC", sans-serif;
      line-height: 1.65;
    }}
    .app {{ width: min(1060px, 100%); margin: 0 auto; padding: 14px 14px 88px; }}
    header {{
      display: grid;
      gap: 18px;
      margin: 8px 0 10px;
      padding: 20px;
      border: 1px solid rgba(255,255,255,0.72);
      border-radius: 24px;
      background:
        linear-gradient(135deg, rgba(255,255,255,0.84), rgba(246,242,255,0.72)),
        linear-gradient(135deg, rgba(77,141,247,0.18), rgba(139,115,239,0.24));
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
    }}
    .title-row {{ display: flex; justify-content: space-between; align-items: flex-end; gap: 14px; flex-wrap: wrap; }}
    h1 {{
      margin: 0;
      font-size: clamp(2rem, 9vw, 3.15rem);
      line-height: 1.08;
      letter-spacing: 0;
      color: #25314c;
      text-shadow: 0 1px 0 rgba(255,255,255,0.85);
    }}
    .built {{ color: #586785; font-size: 0.95rem; margin-top: 8px; }}
    .controls {{ display: grid; grid-template-columns: minmax(150px, 220px) minmax(0, 1fr); gap: 10px; align-items: center; }}
    select, input {{
      min-height: 48px;
      border: 1px solid rgba(205,216,246,0.95);
      border-radius: 14px;
      background: rgba(255,255,255,0.90);
      color: var(--ink);
      font: inherit;
      padding: 10px 14px;
      box-shadow: 0 1px 0 rgba(255,255,255,0.9) inset, 0 6px 16px rgba(89, 104, 154, 0.08);
    }}
    input {{ flex: 1 1 260px; min-width: 0; }}
    .filters {{
      display: flex;
      gap: 10px;
      overflow-x: auto;
      padding: 14px 2px 12px;
      margin: 0 -2px;
      scrollbar-width: thin;
      -webkit-overflow-scrolling: touch;
    }}
    .filter-btn {{
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.92);
      color: #53617c;
      border-radius: 999px;
      padding: 10px 15px;
      white-space: nowrap;
      font: inherit;
      font-weight: 650;
      cursor: pointer;
      box-shadow: 0 6px 16px rgba(84, 99, 152, 0.07);
    }}
    .filter-btn.active {{
      background: linear-gradient(135deg, var(--blue), var(--violet));
      border-color: transparent;
      color: white;
      box-shadow: 0 10px 22px rgba(116, 107, 230, 0.26);
    }}
    .section-title {{
      display: flex;
      align-items: center;
      gap: 9px;
      margin: 22px 0 12px;
      font-size: 1.18rem;
      line-height: 1.35;
      letter-spacing: 0;
    }}
    .section-title::before {{
      content: "";
      width: 7px;
      height: 22px;
      border-radius: 999px;
      background: linear-gradient(180deg, var(--blue), var(--violet));
    }}
    .pinned, .cards {{ display: grid; gap: 14px; }}
    .pinned {{ grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); margin-bottom: 4px; }}
    .card {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 20px;
      background: rgba(255,255,255,0.96);
      box-shadow: var(--shadow-soft);
      overflow: hidden;
      transition: border-color 160ms ease, box-shadow 160ms ease, transform 160ms ease;
    }}
    .card.open {{
      border-color: var(--line-strong);
      box-shadow: var(--shadow);
    }}
    .card:hover {{ transform: translateY(-1px); box-shadow: var(--shadow); }}
    .pinned-card {{
      position: relative;
      border-color: #d6ccff;
      background: linear-gradient(180deg, #fbf9ff 0%, #ffffff 100%);
    }}
    .pinned-card::before {{
      content: "";
      position: absolute;
      inset: 0 auto 0 0;
      width: 5px;
      background: linear-gradient(180deg, var(--violet), var(--blue));
    }}
    .card-toggle {{
      display: block;
      width: 100%;
      text-align: left;
      color: inherit;
      padding: 0;
      border: 0;
      background: transparent;
      cursor: pointer;
      font: inherit;
    }}
    .card-toggle:focus-visible {{ outline: 3px solid rgba(116,107,230,0.38); outline-offset: -5px; border-radius: 18px; }}
    .card-head {{ padding: 18px 18px 17px; }}
    .meta {{ display: flex; gap: 9px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }}
    .chip {{
      color: var(--violet-dark);
      background: var(--violet-soft);
      border: 1px solid #ded6ff;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 0.86rem;
      font-weight: 700;
      line-height: 1.35;
    }}
    .count {{ color: var(--muted); font-size: 0.9rem; line-height: 1.35; }}
    .card h3 {{ margin: 0 0 9px; font-size: 1.18rem; line-height: 1.45; letter-spacing: 0; }}
    .card p {{ margin: 0; color: var(--muted); }}
    .detail {{
      display: none;
      border-top: 1px solid var(--line);
      background: linear-gradient(180deg, var(--panel-soft), #fff 56%);
      padding: 16px 18px 18px;
    }}
    .card.open .detail {{ display: block; }}
    .detail-section {{
      padding: 12px 0;
      border-bottom: 1px solid rgba(217,226,251,0.85);
    }}
    .detail-section:first-child {{ padding-top: 0; }}
    .detail-section:last-child {{ border-bottom: 0; padding-bottom: 0; }}
    .detail h4 {{ margin: 0 0 7px; font-size: 1rem; color: var(--blue-dark); letter-spacing: 0; }}
    .detail p {{ color: var(--ink); margin: 0; }}
    .links {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 2px; }}
    .links a {{
      display: inline-flex;
      align-items: center;
      min-height: 38px;
      color: var(--blue-dark);
      background: white;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 8px 11px;
      text-decoration: none;
      overflow-wrap: anywhere;
      box-shadow: 0 4px 12px rgba(84, 99, 152, 0.07);
    }}
    .empty {{ color: var(--muted); background: var(--violet-soft); border: 1px solid var(--line); border-radius: 18px; padding: 18px; }}
    .error-card {{
      color: #7a3351;
      background: linear-gradient(180deg, var(--rose-soft), #ffffff);
      border: 1px solid #f2c9dc;
      border-radius: 20px;
      box-shadow: var(--shadow-soft);
      padding: 18px;
    }}
    .error-card h3 {{ margin: 0 0 8px; font-size: 1.08rem; }}
    .error-card p {{ margin: 0; color: #86536b; }}
    .loading {{ color: var(--muted); background: rgba(255,255,255,0.9); border: 1px solid var(--line); border-radius: 18px; padding: 18px; }}
    .top-button {{
      position: fixed;
      right: 16px;
      bottom: 18px;
      z-index: 20;
      width: 50px;
      height: 50px;
      border: 0;
      border-radius: 999px;
      color: white;
      background: linear-gradient(135deg, var(--blue), var(--violet));
      box-shadow: 0 14px 30px rgba(94, 100, 220, 0.32);
      font-size: 1.2rem;
      font-weight: 800;
      cursor: pointer;
      opacity: 0;
      pointer-events: none;
      transform: translateY(8px);
      transition: opacity 160ms ease, transform 160ms ease;
    }}
    .top-button.show {{ opacity: 1; pointer-events: auto; transform: translateY(0); }}
    footer {{ color: var(--muted); font-size: 0.9rem; padding: 26px 2px 0; }}
    @media (max-width: 620px) {{
      .app {{ padding: 12px 12px 82px; }}
      header {{ border-radius: 22px; padding: 18px 16px; }}
      .controls {{ display: grid; grid-template-columns: 1fr; }}
      select, input {{ width: 100%; }}
      .pinned {{ grid-template-columns: 1fr; }}
      .card-head {{ padding: 17px 16px; }}
      .detail {{ padding: 15px 16px 17px; }}
      .card h3 {{ font-size: 1.15rem; }}
      .filters {{ padding-bottom: 14px; }}
    }}
  </style>
</head>
<body>
  <div class="app">
    <header>
      <div class="title-row">
        <div>
          <h1>鈴音新聞整理室</h1>
          <div class="built">最後建置時間：<span id="builtAt"></span></div>
        </div>
      </div>
      <div class="controls">
        <select id="dateSelect" aria-label="選擇日期"></select>
        <input id="searchInput" type="search" placeholder="搜尋標題、摘要、來源" aria-label="搜尋新聞">
      </div>
    </header>
    <nav id="filters" class="filters" aria-label="分類篩選"></nav>
    <main>
      <section id="pinnedSection" aria-labelledby="pinnedTitle">
        <h2 id="pinnedTitle" class="section-title">今日最重要 5 件事</h2>
        <div id="pinned" class="pinned"></div>
      </section>
      <section aria-labelledby="cardsTitle">
        <h2 id="cardsTitle" class="section-title">新聞列表</h2>
        <div id="cards" class="cards"></div>
      </section>
      <div id="empty" class="empty" hidden>沒有符合條件的新聞。</div>
    </main>
    <footer>資料來自 Hermes 本機新聞管線；所有來源連結保留於各新聞詳細內容。</footer>
  </div>
  <button id="topButton" class="top-button" type="button" aria-label="回到頂部">↑</button>
  <script>
    const state = {{ date: '', category: 'all', query: '' }};
    const dayCache = new Map();
    let manifest = null;
    const dateSelect = document.getElementById('dateSelect');
    const searchInput = document.getElementById('searchInput');
    const filters = document.getElementById('filters');
    const pinnedEl = document.getElementById('pinned');
    const cardsEl = document.getElementById('cards');
    const emptyEl = document.getElementById('empty');
    const pinnedSection = document.getElementById('pinnedSection');
    const topButton = document.getElementById('topButton');

    document.getElementById('builtAt').textContent = '載入中';

    async function fetchJson(path) {{
      const response = await fetch(path, {{ cache: 'no-cache' }});
      if (!response.ok) {{
        throw new Error(`${{path}} 載入失敗：HTTP ${{response.status}}`);
      }}
      return response.json();
    }}

    async function loadDate(date) {{
      if (dayCache.has(date)) return dayCache.get(date);
      const data = await fetchJson(`data/${{date}}.json`);
      dayCache.set(date, data);
      return data;
    }}

    function showLoading(message) {{
      pinnedSection.hidden = true;
      emptyEl.hidden = true;
      pinnedEl.replaceChildren();
      cardsEl.innerHTML = `<div class="loading">${{escapeHtml(message || '載入中')}}</div>`;
    }}

    function showError(message) {{
      pinnedSection.hidden = true;
      emptyEl.hidden = true;
      pinnedEl.replaceChildren();
      cardsEl.innerHTML = `
        <div class="error-card">
          <h3>資料載入失敗</h3>
          <p>${{escapeHtml(message || '請稍後重試，或確認 data 目錄已產生。')}}</p>
        </div>
      `;
    }}

    function textIncludes(card, query) {{
      if (!query) return true;
      const haystack = [
        card.title, card.summary, card.detailSummary, card.why, card.categoryLabel,
        ...(card.sources || []).map(source => source.source + ' ' + source.url)
      ].join(' ').toLowerCase();
      return haystack.includes(query.toLowerCase());
    }}

    function passes(card) {{
      return (state.category === 'all' || card.category === state.category) && textIncludes(card, state.query);
    }}

    function makeCard(card) {{
      const article = document.createElement('article');
      article.className = card.pinned ? 'card pinned-card' : 'card';
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'card-toggle';
      button.setAttribute('aria-expanded', 'false');
      button.innerHTML = `
        <div class="card-head">
          <div class="meta">
            <span class="chip">${{escapeHtml(card.categoryLabel || '其他')}}</span>
            <span class="count">${{card.sourceCount || 0}} 個來源</span>
          </div>
          <h3>${{escapeHtml(card.title || '未命名新聞')}}</h3>
          <p>${{escapeHtml(card.summary || card.detailSummary || '尚無簡述')}}</p>
        </div>
      `;
      const detail = document.createElement('div');
      detail.className = 'detail';
      detail.innerHTML = `
        <div class="detail-section">
          <h4>鈴音整理</h4>
          <p>${{escapeHtml(card.detailSummary || card.summary || '尚無摘要')}}</p>
        </div>
        ${{card.why ? `<div class="detail-section"><h4>為什麼值得注意</h4><p>${{escapeHtml(card.why)}}</p></div>` : ''}}
        <div class="detail-section">
          <h4>來源報導</h4>
          <div class="links">${{renderLinks(card.sources || [])}}</div>
        </div>
      `;
      article.addEventListener('click', event => {{
        if (event.target.closest('a')) return;
        const open = !article.classList.contains('open');
        article.classList.toggle('open', open);
        button.setAttribute('aria-expanded', open ? 'true' : 'false');
      }});
      article.append(button, detail);
      return article;
    }}

    function renderLinks(sources) {{
      if (!sources.length) return '<span class="count">尚無來源連結</span>';
      return sources.map((source, index) => `
        <a href="${{escapeAttr(source.url)}}" target="_blank" rel="noopener noreferrer">
          ${{escapeHtml(source.source || ('來源 ' + (index + 1)))}}
        </a>
      `).join('');
    }}

    function escapeHtml(value) {{
      return String(value || '').replace(/[&<>"']/g, char => ({{
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }}[char]));
    }}

    function escapeAttr(value) {{
      return escapeHtml(value).replace(/`/g, '&#96;');
    }}

    function render() {{
      const dateData = dayCache.get(state.date) || {{ pinned: [], cards: [] }};
      const pinned = (dateData.pinned || []).filter(passes);
      const cards = (dateData.cards || []).filter(passes);
      pinnedEl.replaceChildren(...pinned.map(makeCard));
      cardsEl.replaceChildren(...cards.map(makeCard));
      pinnedSection.hidden = pinned.length === 0;
      emptyEl.hidden = pinned.length + cards.length > 0;
    }}

    function buildDateSelect(dates) {{
      dateSelect.replaceChildren();
      dates.forEach(date => {{
        const option = document.createElement('option');
        option.value = date;
        option.textContent = date;
        dateSelect.appendChild(option);
      }});
      dateSelect.value = state.date;
    }}

    function buildFilters(categories) {{
      filters.replaceChildren();
      const allButton = document.createElement('button');
      allButton.type = 'button';
      allButton.className = 'filter-btn active';
      allButton.dataset.category = 'all';
      allButton.textContent = '全部';
      filters.appendChild(allButton);
      categories.forEach(category => {{
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'filter-btn';
        button.dataset.category = category.key;
        button.textContent = category.label;
        filters.appendChild(button);
      }});
    }}

    async function selectDate(date) {{
      state.date = date;
      dateSelect.value = date;
      showLoading('載入新聞資料中');
      try {{
        const data = await loadDate(date);
        document.getElementById('builtAt').textContent = data.generated_at || manifest.generated_at || '';
        render();
      }} catch (error) {{
        showError(error.message);
      }}
    }}

    dateSelect.addEventListener('change', () => {{
      selectDate(dateSelect.value);
    }});

    filters.addEventListener('click', event => {{
      const selected = event.target.closest('button[data-category]');
      if (!selected) return;
      state.category = selected.dataset.category;
      filters.querySelectorAll('.filter-btn').forEach(item => item.classList.toggle('active', item === selected));
      render();
    }});
    searchInput.addEventListener('input', () => {{
      state.query = searchInput.value.trim();
      render();
    }});
    topButton.addEventListener('click', () => window.scrollTo({{ top: 0, behavior: 'smooth' }}));
    window.addEventListener('scroll', () => {{
      topButton.classList.toggle('show', window.scrollY > 420);
    }}, {{ passive: true }});

    async function init() {{
      showLoading('載入日期清單中');
      try {{
        manifest = await fetchJson('data/manifest.json');
        document.getElementById('builtAt').textContent = manifest.generated_at || '';
        state.date = manifest.latest || (manifest.dates || [])[0] || '';
        buildDateSelect(manifest.dates || []);
        buildFilters(manifest.categories || []);
        if (!state.date) {{
          showError('manifest.json 沒有可用日期。');
          return;
        }}
        await selectDate(state.date);
      }} catch (error) {{
        showError(error.message + '。若你是用 file:// 開啟，請改用本機 HTTP server 測試 fetch 載入。');
      }}
    }}

    init();
  </script>
</body>
</html>
"""


def main() -> int:
    manifest, date_payloads = build_site_payloads()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for date, payload in date_payloads.items():
        atomic_write_text(DATA_DIR / f"{date}.json", json_text(payload))
    atomic_write_text(MANIFEST_FILE, json_text(manifest))
    atomic_write_text(OUTPUT_FILE, render_html())
    card_count = sum(len(day["pinned"]) + len(day["cards"]) for day in date_payloads.values())
    print(f"建置日期數：{len(manifest['dates'])}")
    print(f"新聞卡片數：{card_count}")
    print(f"輸出路徑：{OUTPUT_FILE}")
    print(f"資料目錄：{DATA_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
