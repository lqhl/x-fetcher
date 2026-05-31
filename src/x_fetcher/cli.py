from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .config import load_config
from .fetcher import Fetcher, count_skippable_windows
from .store import Store
from .validate import validate_range
from .x_client import XClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="x-fetcher")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--secrets", default="secrets.yaml", help="Path to secrets.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create config files and initialize the database")
    init.add_argument("--db", dest="db_path", help="SQLite database path")

    fetch = sub.add_parser("fetch", help="Fetch tweets into SQLite")
    add_target_args(fetch)
    fetch.add_argument("--max-tweets", type=int, help="Maximum tweets per phase/window")
    fetch.add_argument("--force-window", action="store_true", help="Refetch complete windows and upsert tweets")
    fetch.add_argument("--no-validate", action="store_true", help="Skip post-fetch validation")

    validate = sub.add_parser("validate", help="Validate database structure and date coverage")
    add_target_args(validate)

    stats = sub.add_parser("stats", help="Print tweet counts and date ranges")
    stats.add_argument("--user", help="Screen name")
    stats.add_argument("--db", dest="db_path", help="SQLite database path")
    return parser


def add_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--user", help="Screen name to fetch or validate")
    parser.add_argument("--since", help="Inclusive start date, YYYY-MM-DD")
    parser.add_argument("--until", help="Exclusive end date, YYYY-MM-DD")
    parser.add_argument("--db", dest="db_path", help="SQLite database path")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            return cmd_init(args)
        if args.command == "fetch":
            return cmd_fetch(args)
        if args.command == "validate":
            return cmd_validate(args)
        if args.command == "stats":
            return cmd_stats(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    cwd = Path.cwd()
    for name in ("config.yaml", "secrets.yaml"):
        target = cwd / name
        source = cwd / f"{name.split('.')[0]}.example.yaml"
        if not target.exists() and source.exists():
            shutil.copyfile(source, target)
            print(f"created {target}")
    config = load_config(Path(args.config), Path(args.secrets), db_path=args.db_path)
    store = Store(config.db_path)
    store.init_schema()
    store.close()
    print(f"initialized {config.db_path}")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    config = load_config(
        Path(args.config),
        Path(args.secrets),
        user=args.user,
        since=args.since,
        until=args.until,
        db_path=args.db_path,
        max_tweets=args.max_tweets,
        require_secrets=False,
    )
    target = config.target_for(args.user)
    store = Store(config.db_path)
    store.init_schema()
    try:
        if not args.force_window:
            skipped_windows = count_skippable_windows(store, target, config.window_days)
            if skipped_windows is not None:
                print(
                    "fetch complete: "
                    f"inserted=0 updated=0 fetched_windows=0 skipped_windows={skipped_windows}"
                )
                if not args.no_validate:
                    report = validate_range(store, target.screen_name, target.since, target.until, config.window_days)
                    print_validation(report)
                    return 0 if report.ok else 2
                return 0
        if config.secrets is None:
            raise ValueError("secrets.yaml is required for fetch")
        fetcher = Fetcher(config, store, XClient(config.secrets))
        result = fetcher.fetch_target(target, force_window=args.force_window)
        print(
            "fetch complete: "
            f"inserted={result.inserted} updated={result.updated} "
            f"fetched_windows={result.fetched_windows} skipped_windows={result.skipped_windows}"
        )
        if result.reasons:
            for reason in result.reasons:
                print(f"coverage note: {reason}")
        if not args.no_validate:
            report = validate_range(store, target.screen_name, target.since, target.until, config.window_days)
            print_validation(report)
            return 0 if report.ok else 2
        return 0
    finally:
        store.close()


def cmd_validate(args: argparse.Namespace) -> int:
    config = load_config(
        Path(args.config),
        Path(args.secrets),
        user=args.user,
        since=args.since,
        until=args.until,
        db_path=args.db_path,
    )
    target = config.target_for(args.user)
    store = Store(config.db_path)
    store.init_schema()
    try:
        report = validate_range(store, target.screen_name, target.since, target.until, config.window_days)
        print_validation(report)
        return 0 if report.ok else 2
    finally:
        store.close()


def cmd_stats(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config), Path(args.secrets), user=args.user, db_path=args.db_path)
    store = Store(config.db_path)
    store.init_schema()
    try:
        user_id = store.get_user_id(args.user) if args.user else None
        if args.user and user_id is None:
            raise ValueError(f"unknown user @{args.user}")
        stats = store.stats(user_id)
        print(f"tweets={stats['count']} oldest={stats['oldest']} newest={stats['newest']}")
        for month, count in stats["months"].items():  # type: ignore[union-attr]
            print(f"{month}: {count}")
        return 0
    finally:
        store.close()


def print_validation(report) -> None:
    print(f"validation {'ok' if report.ok else 'failed'}: tweets={report.total} oldest={report.oldest} newest={report.newest}")
    if report.monthly_counts:
        print("monthly distribution:")
        for month, count in report.monthly_counts.items():
            print(f"  {month}: {count}")
    for label, values in (
        ("errors", report.errors),
        ("missing_windows", report.missing_windows),
        ("partial_windows", report.partial_windows),
        ("failed_windows", report.failed_windows),
        ("suspicious_windows", report.suspicious_windows),
    ):
        if values:
            print(f"{label}:")
            for value in values:
                print(f"  {value}")


if __name__ == "__main__":
    raise SystemExit(main())
