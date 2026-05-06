#!/usr/bin/env python3
"""Check whether daily Hermes news category outputs are fresh enough."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "news_sources.json"


def load_config(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
    except FileNotFoundError as exc:
        raise ValueError(f"config not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"config is not valid JSON: {path}: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("config root must be an object")
    if "categories" not in config or not isinstance(config["categories"], dict):
        raise ValueError("config.categories must be an object")
    if "archive_dir" not in config:
        raise ValueError("config.archive_dir is required")
    return config


def default_date(config: dict[str, Any]) -> str:
    tz_name = str(config.get("timezone") or "Asia/Taipei")
    return datetime.now(ZoneInfo(tz_name)).date().isoformat()


def count_jsonl_lines(path: Path) -> tuple[int, str | None]:
    count = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    count += 1
    except OSError as exc:
        return 0, f"cannot read jsonl: {exc}"
    return count, None


def load_status(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "status file missing"
    except json.JSONDecodeError as exc:
        return None, f"status JSON invalid: {exc}"
    except OSError as exc:
        return None, f"cannot read status: {exc}"
    if not isinstance(data, dict):
        return None, "status JSON root is not an object"
    return data, None


def source_failure_count(status: dict[str, Any] | None) -> int:
    sources = (status or {}).get("sources", [])
    if not isinstance(sources, list):
        return 0
    return sum(1 for source in sources if isinstance(source, dict) and not source.get("ok"))


def build_report(config: dict[str, Any], date: str, min_items: int) -> dict[str, Any]:
    archive_dir = Path(str(config["archive_dir"]))
    daily_dir = archive_dir / "daily" / date
    categories_cfg = config["categories"]
    report: dict[str, Any] = {
        "date": date,
        "archive_dir": str(archive_dir),
        "daily_dir": str(daily_dir),
        "min_items": min_items,
        "ok": True,
        "warnings": [],
        "severe": [],
        "categories": {},
        "usable_categories": 0,
        "total_items": 0,
    }

    if not daily_dir.exists():
        report["severe"].append(f"daily dir missing: {daily_dir}")
        report["ok"] = False

    for category, cat_cfg in categories_cfg.items():
        label = str(cat_cfg.get("label") or category)
        jsonl_path = daily_dir / f"{category}.jsonl"
        status_path = daily_dir / f"{category}.status.json"
        cat_warnings: list[str] = []
        cat_severe: list[str] = []
        line_count = 0
        status: dict[str, Any] | None = None
        status_items: int | None = None

        jsonl_exists = jsonl_path.exists()
        status_exists = status_path.exists()
        if not jsonl_exists:
            cat_warnings.append(f"{category}.jsonl missing")
        else:
            line_count, line_error = count_jsonl_lines(jsonl_path)
            if line_error:
                cat_severe.append(line_error)

        if not status_exists:
            cat_warnings.append(f"{category}.status.json missing")
        else:
            status, status_error = load_status(status_path)
            if status_error:
                cat_severe.append(status_error)
            else:
                raw_items = status.get("items")
                if isinstance(raw_items, int):
                    status_items = raw_items
                    if status_items != line_count:
                        cat_warnings.append(f"status.items={status_items} differs from jsonl lines={line_count}")
                else:
                    cat_warnings.append("status.items missing or not an integer")

        if jsonl_exists and line_count < min_items:
            cat_warnings.append(f"jsonl line count {line_count} below min-items {min_items}")

        source_failures = source_failure_count(status)
        if source_failures:
            cat_warnings.append(f"{source_failures} source(s) failed")

        usable = jsonl_exists and line_count >= min_items and not cat_severe
        if usable:
            report["usable_categories"] += 1
        report["total_items"] += line_count

        category_report = {
            "category": category,
            "label": label,
            "jsonl": str(jsonl_path),
            "jsonl_exists": jsonl_exists,
            "line_count": line_count,
            "status": str(status_path),
            "status_exists": status_exists,
            "status_parseable": bool(status_exists and status is not None),
            "status_items": status_items,
            "source_failures": source_failures,
            "usable": usable,
            "warnings": cat_warnings,
            "severe": cat_severe,
        }
        report["categories"][category] = category_report
        report["warnings"].extend(f"{category}: {warning}" for warning in cat_warnings)
        report["severe"].extend(f"{category}: {error}" for error in cat_severe)

    if report["usable_categories"] == 0:
        report["severe"].append("all categories have no usable items")
    report["ok"] = not report["severe"]
    return report


def print_human_report(report: dict[str, Any]) -> None:
    status = "OK" if report["ok"] else "SEVERE"
    print(f"新聞資料 freshness check：{status}")
    print(f"- 日期：{report['date']}")
    print(f"- 每日資料夾：{report['daily_dir']}")
    print(f"- 可用分類：{report['usable_categories']}/{len(report['categories'])}")
    print(f"- 總筆數：{report['total_items']}")
    for category, cat in report["categories"].items():
        marker = "OK" if cat["usable"] else "WARN"
        if cat["severe"]:
            marker = "SEVERE"
        print(
            f"- {category}（{cat['label']}）：{marker}，"
            f"items={cat['line_count']}，status_items={cat['status_items']}，"
            f"source_failures={cat['source_failures']}"
        )
        for warning in cat["warnings"]:
            print(f"  warning: {warning}")
        for error in cat["severe"]:
            print(f"  severe: {error}")
    if report["severe"]:
        print("Severe:")
        for error in report["severe"]:
            print(f"- {error}")
    elif report["warnings"]:
        print("Warnings:")
        for warning in report["warnings"]:
            print(f"- {warning}")
    summary = {
        "date": report["date"],
        "ok": report["ok"],
        "warnings": len(report["warnings"]),
        "severe": len(report["severe"]),
        "usable_categories": report["usable_categories"],
        "total_items": report["total_items"],
    }
    print("SUMMARY_JSON=" + json.dumps(summary, ensure_ascii=False, separators=(",", ":")))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check Hermes daily news output freshness.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--date", default=None, help="Archive date YYYY-MM-DD; default today in config timezone.")
    parser.add_argument("--min-items", type=int, default=1)
    parser.add_argument("--json", action="store_true", help="Only output JSON report.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 on warnings as well as severe errors.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.min_items < 0:
        print("--min-items must be >= 0", file=sys.stderr)
        return 2
    try:
        config = load_config(Path(args.config))
        date = args.date or default_date(config)
    except Exception as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    report = build_report(config, date, args.min_items)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    else:
        print_human_report(report)

    if report["severe"]:
        return 1
    if args.strict and report["warnings"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
