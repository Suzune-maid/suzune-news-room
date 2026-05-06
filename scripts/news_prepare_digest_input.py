#!/usr/bin/env python3
"""Prepare a compact LLM input from collected category JSONL files."""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "news_sources.json"


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_title(title: str) -> str:
    title = re.sub(r"[\s\u3000，。！？、；：:;,.!?｜|\-—_\[\]【】（）()「」『』\"'`]+", "", title.lower())
    return title[:70]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def item_sort_key(item: dict[str, Any]) -> str:
    # ISO timestamps sort lexicographically well enough; empty values go last by prefix.
    published = item.get("published") or ""
    collected = item.get("collected_at") or ""
    return published or collected


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare daily news digest input for a Hermes cron agent.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--date", help="YYYY-MM-DD; default is today in configured timezone.")
    parser.add_argument("--max-per-category", type=int, default=18)
    parser.add_argument("--max-total", type=int, default=90)
    args = parser.parse_args()

    config = load_config(Path(args.config))
    tz_name = config.get("timezone", "Asia/Taipei")
    run_date = args.date or datetime.now(ZoneInfo(tz_name)).date().isoformat()
    archive_dir = Path(config.get("archive_dir", "/home/atmjin/.hermes/archive/news"))
    daily_dir = archive_dir / "daily" / run_date
    digest_dir = archive_dir / "digests"
    digest_dir.mkdir(parents=True, exist_ok=True)
    draft_path = digest_dir / f"{run_date}-digest-input.md"
    final_path = digest_dir / f"{run_date}-morning.md"

    categories_cfg = config.get("categories", {})
    by_category: dict[str, list[dict[str, Any]]] = {}
    missing: list[str] = []
    statuses: list[str] = []

    global_seen_url: set[str] = set()
    global_seen_title: set[str] = set()
    total = 0

    for category, cat_cfg in categories_cfg.items():
        label = cat_cfg.get("label", category)
        path = daily_dir / f"{category}.jsonl"
        status_path = daily_dir / f"{category}.status.json"
        if not path.exists():
            missing.append(f"{category}（{label}）")
            by_category[category] = []
            continue
        rows = read_jsonl(path)
        rows.sort(key=item_sort_key, reverse=True)
        kept: list[dict[str, Any]] = []
        for row in rows:
            url = row.get("url") or ""
            title_key = row.get("title_key") or normalize_title(row.get("title", ""))
            if url and url in global_seen_url:
                continue
            if title_key and title_key in global_seen_title:
                continue
            if url:
                global_seen_url.add(url)
            if title_key:
                global_seen_title.add(title_key)
            kept.append(row)
            total += 1
            if len(kept) >= args.max_per_category or total >= args.max_total:
                break
        by_category[category] = kept
        if status_path.exists():
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
                failed = [s for s in status.get("sources", []) if not s.get("ok")]
                if failed:
                    statuses.append(f"{label}: {len(failed)} 個來源失敗（仍保留已抓到資料）")
            except Exception:
                pass
        if total >= args.max_total:
            break

    lines: list[str] = []
    lines.append(f"# 每日新聞整理輸入｜{run_date}")
    lines.append("")
    lines.append("以下是 RSS 採集後的候選新聞。請整理成給主人的台灣繁體中文新聞簡報，不要逐篇照抄。")
    lines.append("")
    lines.append("## 整理要求")
    lines.append("- 先列「今日最重要 5 件事」。")
    lines.append("- 再依分類整理：台灣／公共議題、國際、財經／市場、科技／AI、社會／生活。")
    lines.append("- 同一事件多來源報導請合併，只保留代表性來源連結。")
    lines.append("- 每則用 1～2 句摘要，加一句「為什麼值得注意」。")
    lines.append("- 若資料不足、來源失敗或分類空白，請明確標註。")
    lines.append("- 使用台灣繁體中文，避免簡體字與中國常用語彙。")
    lines.append("")
    lines.append(f"建議最終摘要存檔路徑：`{final_path}`")
    lines.append("")

    if missing or statuses:
        lines.append("## 採集狀態")
        if missing:
            lines.append("- 缺少分類檔案：" + "、".join(missing))
        for s in statuses:
            lines.append(f"- {s}")
        lines.append("")

    for category, cat_cfg in categories_cfg.items():
        label = cat_cfg.get("label", category)
        rows = by_category.get(category, [])
        lines.append(f"## {label}（{category}）")
        if not rows:
            lines.append("- 沒有可用資料。")
            lines.append("")
            continue
        for i, row in enumerate(rows, 1):
            title = row.get("title", "").strip() or "（無標題）"
            source = row.get("source", "未知來源")
            published = row.get("published", "")
            url = row.get("url", "")
            summary = row.get("summary", "")
            lines.append(f"{i}. {title}")
            lines.append(f"   - 來源：{source}")
            if published:
                lines.append(f"   - 時間：{published}")
            if summary:
                lines.append(f"   - 摘要線索：{summary[:220]}")
            if url:
                lines.append(f"   - 連結：{url}")
        lines.append("")

    output = "\n".join(lines).rstrip() + "\n"
    draft_path.write_text(output, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
