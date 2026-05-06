#!/usr/bin/env python3
"""Copy generated Hermes news static-site files into this repo and push them.

This script intentionally stages only the deployed static assets:
- index.html
- data/*.json

It is safe for a cron job to run after the local Hermes pipeline rebuilds
/home/atmjin/.hermes/archive/news/site.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if check and result.returncode != 0:
        raise SystemExit(
            f"Command failed ({result.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


def copy_site(source: Path, repo: Path) -> list[str]:
    if not (source / "index.html").is_file():
        raise SystemExit(f"Missing source index.html: {source / 'index.html'}")
    if not (source / "data" / "manifest.json").is_file():
        raise SystemExit(f"Missing source manifest.json: {source / 'data' / 'manifest.json'}")
    if not (repo / ".git").exists():
        raise SystemExit(f"Target is not a git repository: {repo}")

    changed_paths: list[str] = []

    def copy_one(src: Path, dst: Path, rel: str) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        before = dst.read_bytes() if dst.exists() else None
        shutil.copy2(src, dst)
        after = dst.read_bytes()
        if before != after:
            changed_paths.append(rel)

    copy_one(source / "index.html", repo / "index.html", "index.html")

    source_data = source / "data"
    repo_data = repo / "data"
    repo_data.mkdir(parents=True, exist_ok=True)
    source_json_names = {p.name for p in source_data.glob("*.json")}
    for src in sorted(source_data.glob("*.json")):
        copy_one(src, repo_data / src.name, f"data/{src.name}")

    # Remove date/data JSON files no longer emitted by the builder, but keep no other files.
    for existing in repo_data.glob("*.json"):
        if existing.name not in source_json_names:
            existing.unlink()
            changed_paths.append(f"data/{existing.name}")

    return sorted(set(changed_paths))


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish generated Hermes news site to GitHub Pages repo")
    parser.add_argument("--source", default="/home/atmjin/.hermes/archive/news/site", help="Generated site directory")
    parser.add_argument("--repo", default="/home/atmjin/.hermes/archive/github/suzune-news-room", help="GitHub Pages repo directory")
    parser.add_argument("--message", default=None, help="Commit message")
    parser.add_argument("--dry-run", action="store_true", help="Copy and show status without committing/pushing")
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    repo = Path(args.repo).expanduser().resolve()

    changed_paths = copy_site(source, repo)
    status = run(["git", "status", "--short", "--", "index.html", "data"], cwd=repo).stdout.strip()
    if not status:
        print("No site changes to publish.")
        return 0

    print("Site asset changes:")
    print(status)

    if args.dry_run:
        print("Dry run: not committing or pushing.")
        return 0

    run(["git", "add", "index.html", "data"], cwd=repo)
    commit_msg = args.message or f"Update news site {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %z')}"
    run(["git", "commit", "-m", commit_msg], cwd=repo)
    run(["git", "push", "origin", "main"], cwd=repo)
    print("Published site changes to GitHub Pages repo.")
    if changed_paths:
        print("Updated files: " + ", ".join(changed_paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
