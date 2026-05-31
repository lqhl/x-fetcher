from __future__ import annotations

import random
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .config import AppConfig, TargetConfig
from .models import Tweet
from .parser import collect_entries, parse_entry, search_instructions, user_timeline_instructions
from .store import Store, UpsertStats
from .x_client import XClient, XClientError, XResponse


class FetchError(RuntimeError):
    pass


class PartialFetchError(FetchError):
    def __init__(self, message: str, tweets: list[Tweet], last_cursor: str | None):
        super().__init__(message)
        self.tweets = tweets
        self.last_cursor = last_cursor


@dataclass
class FetchResult:
    inserted: int = 0
    updated: int = 0
    skipped_windows: int = 0
    fetched_windows: int = 0
    partial_windows: int = 0
    reasons: list[str] = field(default_factory=list)

    def add_stats(self, stats: UpsertStats) -> None:
        self.inserted += stats.inserted
        self.updated += stats.updated


class Fetcher:
    def __init__(self, config: AppConfig, store: Store, client: XClient, *, sleep_enabled: bool = True):
        self.config = config
        self.store = store
        self.client = client
        self.sleep_enabled = sleep_enabled

    def fetch_target(self, target: TargetConfig, *, force_window: bool = False) -> FetchResult:
        profile = self.client.get_user_profile(target.screen_name)
        user_id = self.store.upsert_user(profile)
        result = FetchResult()

        print(f"Finding UserTweets query id for @{target.screen_name}...", file=sys.stderr)
        user_qid = self.client.find_operation_query_id(target.screen_name, "UserTweets")
        print("Fetching recent timeline...", file=sys.stderr)
        recent = self._fetch_recent_timeline(user_id, profile.rest_id, user_qid, target)
        result.add_stats(self.store.upsert_tweets(user_id, recent))

        print(f"Finding SearchTimeline query id for @{target.screen_name}...", file=sys.stderr)
        search_qid = self.client.find_operation_query_id(target.screen_name, "SearchTimeline")
        for since, until in split_windows(target.since, target.until, self.config.window_days):
            row = self.store.window_status(user_id, since, until)
            if not force_window and is_skippable_window(self.store, user_id, since, until):
                result.skipped_windows += 1
                continue
            initial_cursor = row["last_cursor"] if row and row["status"] in ("partial", "failed") else None
            try:
                tweets, last_cursor, reason = self._fetch_search_window(
                    target.screen_name,
                    search_qid,
                    since,
                    until,
                    initial_cursor=initial_cursor,
                )
                stats = self.store.upsert_tweets(user_id, tweets)
                result.add_stats(stats)
                self.store.complete_window(user_id, since, until, tweets, last_cursor, reason)
                result.fetched_windows += 1
                if reason:
                    result.reasons.append(f"{since}/{until}: {reason}")
                self._sleep_range(self.config.request_sleep.window)
            except PartialFetchError as exc:
                stats = self.store.upsert_tweets(user_id, exc.tweets)
                result.add_stats(stats)
                self.store.partial_window(user_id, since, until, exc.tweets, exc.last_cursor, str(exc))
                result.partial_windows += 1
                raise
            except FetchError as exc:
                self.store.failed_window(user_id, since, until, str(exc))
                raise
        return result

    def _fetch_recent_timeline(self, user_id: int, rest_id: str, query_id: str, target: TargetConfig) -> list[Tweet]:
        tweets: list[Tweet] = []
        cursor = None
        known_only_pages = 0
        page = 0
        limit = self.config.max_tweets
        while limit is None or len(tweets) < limit:
            if page >= self.config.user_tweets_max_pages:
                break
            resp = self._request_with_backoff(lambda: self.client.user_tweets(query_id, rest_id, cursor))
            data = resp.data or {}
            page += 1
            page_tweets, cursor = parse_timeline_page(data)
            in_range = [t for t in page_tweets if date_in_range(t, target.since, target.until)]
            unknown = [t for t in in_range if not self.store.has_tweet(user_id, t.status_id)]
            known_only_pages = known_only_pages + 1 if in_range and not unknown else 0
            tweets.extend(unknown)

            oldest = min((t.created_at_utc.date() for t in page_tweets), default=None)
            print(f"  UserTweets page {page}: +{len(unknown)} new, cursor={bool(cursor)}, oldest={oldest}", file=sys.stderr)
            if not cursor or not page_tweets:
                break
            if oldest and oldest < target.since:
                break
            if known_only_pages >= self.config.user_tweets_stop_known_pages:
                break
            self._sleep_range(self.config.request_sleep.user_tweets)
        return tweets[:limit] if limit else tweets

    def _fetch_search_window(
        self,
        screen_name: str,
        query_id: str,
        since: date,
        until: date,
        *,
        initial_cursor: str | None = None,
    ) -> tuple[list[Tweet], str | None, str | None]:
        raw_query = f"from:{screen_name} since:{since.isoformat()} until:{until.isoformat()}"
        tweets: list[Tweet] = []
        cursor = initial_cursor
        page = 0
        empty_pages = 0
        reason = None
        limit = self.config.max_tweets
        while limit is None or len(tweets) < limit:
            try:
                resp = self._request_with_backoff(lambda: self.client.search_timeline(query_id, raw_query, cursor))
            except FetchError as exc:
                if tweets or cursor:
                    raise PartialFetchError(str(exc), tweets, cursor) from exc
                raise
            page += 1
            page_tweets, cursor = parse_search_page(resp.data or {})
            page_tweets = [t for t in page_tweets if date_in_range(t, since, until)]
            tweets.extend(page_tweets)
            empty_pages = empty_pages + 1 if not page_tweets else 0
            oldest = min((t.created_at_utc.date() for t in page_tweets), default=None)
            print(
                f"  Search {since}..{until} page {page}: +{len(page_tweets)}, cursor={bool(cursor)}, oldest={oldest}",
                file=sys.stderr,
            )
            if not cursor:
                reason = "search returned no more cursor"
                break
            if empty_pages >= 3:
                reason = "stopped after 3 consecutive empty pages"
                break
            self._sleep_range(self.config.request_sleep.search)
        return tweets[:limit] if limit else tweets, cursor, reason

    def _request_with_backoff(self, call) -> XResponse:
        rate_attempt = service_attempt = 0
        while True:
            resp = call()
            if resp.status_code == 200:
                return resp
            if resp.status_code in (401, 403):
                raise FetchError(f"X authentication failed (HTTP {resp.status_code}); refresh cookies")
            if resp.status_code == 429:
                if rate_attempt >= self.config.backoff.max_consecutive:
                    raise FetchError("rate limit persisted after maximum retries")
                wait = self.config.backoff.rate_limit_seconds[min(rate_attempt, len(self.config.backoff.rate_limit_seconds) - 1)]
                rate_attempt += 1
                sleep_for = wait + random.uniform(0, 30)
                print(f"  HTTP 429 rate limited; sleeping {sleep_for:.0f}s before retry {rate_attempt}", file=sys.stderr)
                self._sleep_seconds(sleep_for)
                continue
            if resp.status_code == 503:
                if service_attempt >= self.config.backoff.max_consecutive:
                    raise FetchError("service unavailable persisted after maximum retries")
                low, high = self.config.backoff.service_unavailable_seconds
                service_attempt += 1
                sleep_for = random.uniform(low, high)
                print(f"  HTTP 503 unavailable; sleeping {sleep_for:.0f}s before retry {service_attempt}", file=sys.stderr)
                self._sleep_seconds(sleep_for)
                continue
            raise FetchError(f"X API HTTP {resp.status_code}: {resp.text[:240]}")

    def _sleep_range(self, bounds: tuple[float, float]) -> None:
        self._sleep_seconds(random.uniform(bounds[0], bounds[1]))

    def _sleep_seconds(self, seconds: float) -> None:
        if self.sleep_enabled and seconds > 0:
            time.sleep(seconds)


def parse_timeline_page(data: dict) -> tuple[list[Tweet], str | None]:
    tweets: list[Tweet] = []
    cursor = None
    for entry in collect_entries(user_timeline_instructions(data)):
        entry_tweets, entry_cursor = parse_entry(entry)
        if entry_cursor:
            cursor = entry_cursor
        tweets.extend(entry_tweets)
    return tweets, cursor


def parse_search_page(data: dict) -> tuple[list[Tweet], str | None]:
    tweets: list[Tweet] = []
    cursor = None
    for entry in collect_entries(search_instructions(data)):
        entry_tweets, entry_cursor = parse_entry(entry)
        if entry_cursor:
            cursor = entry_cursor
        tweets.extend(entry_tweets)
    return tweets, cursor


def date_in_range(tweet: Tweet, since: date, until: date) -> bool:
    d = tweet.created_at_utc.date()
    return since <= d < until


def split_windows(since: date, until: date, window_days: int) -> list[tuple[date, date]]:
    if since >= until:
        raise ValueError("since must be earlier than until")
    windows = []
    current = since
    while current < until:
        end = min(current + timedelta(days=window_days), until)
        windows.append((current, end))
        current = end
    return windows


def is_skippable_window(store: Store, user_id: int, since: date, until: date) -> bool:
    return is_skippable_range(store, user_id, since, until)


def count_skippable_windows(store: Store, target: TargetConfig, window_days: int) -> int | None:
    user_id = store.get_user_id(target.screen_name)
    if user_id is None:
        return None

    windows = split_windows(target.since, target.until, window_days)
    if is_skippable_range(store, user_id, target.since, target.until):
        return len(windows)
    return None


def is_skippable_range(store: Store, user_id: int, since: date, until: date) -> bool:
    covered_until = since
    for row in store.complete_windows_overlapping(user_id, since, until):
        row_since = date.fromisoformat(row["since_date"])
        row_until = date.fromisoformat(row["until_date"])
        if row_until <= covered_until:
            continue
        if row_since > covered_until:
            return False
        if int(row["tweet_count"] or 0) > 0 and not store.window_has_tweets(user_id, row_since, row_until):
            continue
        covered_until = row_until
        if covered_until >= until:
            return True
    return False
