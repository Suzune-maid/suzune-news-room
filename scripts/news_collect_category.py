#!/usr/bin/env python3
"""Collect one category of RSS/Atom news into dated JSONL files.

No third-party dependencies: uses urllib + ElementTree only.
"""
from __future__ import annotations

import argparse
import email.utils
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "news_sources.json"
USER_AGENT = "Mozilla/5.0 (Hermes news collector; +https://hermes-agent.nousresearch.com)"


@dataclass
class SourceResult:
    name: str
    url: str
    ok: bool
    found: int = 0
    kept: int = 0
    error: str | None = None
    fetched_at: str = ""


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def taipei_now(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_title(title: str) -> str:
    title = clean_text(title).lower()
    title = re.sub(r"[\s\u3000，。！？、；：:;,.!?｜|\-—_\[\]【】（）()「」『』\"'`]+", "", title)
    return title[:80]


def parse_date(value: str | None) -> str:
    if not value:
        return ""
    value = clean_text(value)
    try:
        dt = email.utils.parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        pass
    # Common Atom / ISO cases.
    for candidate in (value, value.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate).isoformat()
        except Exception:
            continue
    return value


def fetch(url: str, timeout: int) -> bytes:
    ascii_url = urllib.parse.quote(url, safe=":/?&=%#[]@!$&'()*+,;")
    req = urllib.request.Request(ascii_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(2_500_000)


def child_text(elem: ET.Element, names: list[str]) -> str:
    # Match both namespaced and non-namespaced tags by localname.
    wanted = set(names)
    for child in list(elem):
        local = child.tag.rsplit("}", 1)[-1]
        if local in wanted:
            return clean_text("".join(child.itertext()))
    return ""


def child_attr(elem: ET.Element, tag_name: str, attr: str, rel: str | None = None) -> str:
    for child in list(elem):
        local = child.tag.rsplit("}", 1)[-1]
        if local != tag_name:
            continue
        if rel is not None and child.attrib.get("rel") not in (rel, None, "alternate"):
            continue
        val = child.attrib.get(attr)
        if val:
            return clean_text(val)
    return ""


def parse_feed(data: bytes, source_name: str, source_url: str, category: str, category_label: str) -> list[dict[str, Any]]:
    root = ET.fromstring(data)
    root_local = root.tag.rsplit("}", 1)[-1].lower()
    items: list[ET.Element]
    mode: str
    if root_local == "rss":
        channel = next((c for c in list(root) if c.tag.rsplit("}", 1)[-1].lower() == "channel"), root)
        items = [c for c in list(channel) if c.tag.rsplit("}", 1)[-1].lower() == "item"]
        mode = "rss"
    elif root_local == "feed":
        items = [c for c in list(root) if c.tag.rsplit("}", 1)[-1].lower() == "entry"]
        mode = "atom"
    else:
        # Some feeds wrap strangely; search shallowly for item/entry.
        items = [e for e in root.iter() if e.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}]
        mode = "mixed"

    parsed: list[dict[str, Any]] = []
    for item in items:
        title = child_text(item, ["title"])
        if mode == "atom":
            link = child_attr(item, "link", "href", rel="alternate") or child_text(item, ["link"])
            published_raw = child_text(item, ["published", "updated"])
            summary = child_text(item, ["summary", "content"])
        else:
            link = child_text(item, ["link"])
            published_raw = child_text(item, ["pubDate", "published", "updated", "date"])
            summary = child_text(item, ["description", "summary", "content"])
        if not title and not link:
            continue
        parsed.append(
            {
                "category": category,
                "category_label": category_label,
                "source": source_name,
                "source_feed": source_url,
                "title": title,
                "url": link,
                "published": parse_date(published_raw),
                "summary": summary[:500],
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "title_key": normalize_title(title),
            }
        )
    return parsed


def atomic_write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(path)


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def source_health_entry(category: str, label: str, result: SourceResult) -> dict[str, Any]:
    return {
        "category": category,
        "category_label": label,
        "name": result.name,
        "source": result.name,
        "url": result.url,
        "ok": result.ok,
        "found": result.found,
        "kept": result.kept,
        "error": result.error,
        "fetched_at": result.fetched_at,
    }


def update_source_health(
    archive_dir: Path,
    run_date: str,
    category: str,
    label: str,
    results: list[SourceResult],
    tz_name: str,
) -> None:
    health_path = archive_dir / "source_health" / f"{run_date}.json"
    existing: dict[str, Any] = {}
    if health_path.exists():
        try:
            loaded = json.loads(health_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except json.JSONDecodeError:
            existing = {}

    categories = existing.get("categories")
    if not isinstance(categories, dict):
        categories = {}

    entries = [source_health_entry(category, label, result) for result in results]
    categories[category] = {
        "label": label,
        "updated_at": datetime.now(ZoneInfo(tz_name)).isoformat(),
        "sources": entries,
        "ok_sources": sum(1 for entry in entries if entry["ok"]),
        "failed_sources": sum(1 for entry in entries if not entry["ok"]),
        "found": sum(int(entry["found"] or 0) for entry in entries),
        "kept": sum(int(entry["kept"] or 0) for entry in entries),
    }

    flat_sources: list[dict[str, Any]] = []
    for category_key in sorted(categories):
        cat_sources = categories[category_key].get("sources", [])
        if isinstance(cat_sources, list):
            flat_sources.extend(source for source in cat_sources if isinstance(source, dict))

    atomic_write_json(
        health_path,
        {
            "date": run_date,
            "updated_at": datetime.now(ZoneInfo(tz_name)).isoformat(),
            "categories": categories,
            "sources": flat_sources,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect RSS/Atom news for one configured category.")
    parser.add_argument("category", help="Category key in news_sources.json, or 'all'.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--date", help="Archive date YYYY-MM-DD; default is today in configured timezone.")
    parser.add_argument("--timeout", type=int, default=18)
    parser.add_argument("--max-per-source", type=int, default=None)
    parser.add_argument("--max-total", type=int, default=80)
    args = parser.parse_args()

    config = load_config(Path(args.config))
    tz_name = config.get("timezone", "Asia/Taipei")
    run_date = args.date or taipei_now(tz_name).date().isoformat()
    archive_dir = Path(config.get("archive_dir", "/home/atmjin/.hermes/archive/news"))
    categories = config["categories"]

    wanted = list(categories) if args.category == "all" else [args.category]
    bad = [c for c in wanted if c not in categories]
    if bad:
        print(f"未知分類：{', '.join(bad)}；可用分類：{', '.join(categories)}", file=sys.stderr)
        return 2

    overall_exit = 0
    summaries: list[str] = []
    for category in wanted:
        cat_cfg = categories[category]
        label = cat_cfg.get("label", category)
        max_per_source = args.max_per_source or int(cat_cfg.get("max_per_source", 12))
        seen_url: set[str] = set()
        seen_title: set[str] = set()
        rows: list[dict[str, Any]] = []
        results: list[SourceResult] = []

        for source in cat_cfg.get("sources", []):
            name = source["name"]
            url = source["url"]
            fetched_at = datetime.now(ZoneInfo(tz_name)).isoformat()
            try:
                data = fetch(url, args.timeout)
                parsed = parse_feed(data, name, url, category, label)
                kept = 0
                for row in parsed[:max_per_source]:
                    url_key = row.get("url") or ""
                    title_key = row.get("title_key") or ""
                    if url_key and url_key in seen_url:
                        continue
                    if title_key and title_key in seen_title:
                        continue
                    if url_key:
                        seen_url.add(url_key)
                    if title_key:
                        seen_title.add(title_key)
                    rows.append(row)
                    kept += 1
                    if len(rows) >= args.max_total:
                        break
                results.append(SourceResult(name=name, url=url, ok=True, found=len(parsed), kept=kept, fetched_at=fetched_at))
            except Exception as exc:
                # A single feed failing is expected for news RSS. Keep the category usable
                # as long as at least one source still produced rows.
                results.append(
                    SourceResult(
                        name=name,
                        url=url,
                        ok=False,
                        error=f"{type(exc).__name__}: {exc}",
                        fetched_at=fetched_at,
                    )
                )
            if len(rows) >= args.max_total:
                break
            time.sleep(0.2)

        if not rows:
            overall_exit = 1

        out_path = archive_dir / "daily" / run_date / f"{category}.jsonl"
        status_path = archive_dir / "daily" / run_date / f"{category}.status.json"
        atomic_write_jsonl(out_path, rows)
        atomic_write_json(
            status_path,
            {
                "category": category,
                "label": label,
                "date": run_date,
                "collected_at": datetime.now(ZoneInfo(tz_name)).isoformat(),
                "output": str(out_path),
                "items": len(rows),
                "sources": [r.__dict__ for r in results],
            },
        )
        update_source_health(archive_dir, run_date, category, label, results, tz_name)
        ok_count = sum(1 for r in results if r.ok)
        fail_count = len(results) - ok_count
        summaries.append(f"{category}（{label}）：{len(rows)} 則，來源成功 {ok_count}、失敗 {fail_count} → {out_path}")

    print("新聞採集完成")
    for line in summaries:
        print(f"- {line}")
    return overall_exit


if __name__ == "__main__":
    raise SystemExit(main())
