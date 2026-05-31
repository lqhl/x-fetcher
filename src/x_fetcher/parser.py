from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from .models import Tweet


def parse_x_datetime(raw: str) -> datetime:
    if not raw:
        raise ValueError("empty X datetime")
    dt = parsedate_to_datetime(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_tweet_result(result: dict[str, Any]) -> Tweet | None:
    tweet = result.get("tweet", result) if isinstance(result, dict) else {}
    if "rest_id" not in tweet and isinstance(result, dict):
        tweet = result.get("tweet", {})
    if not isinstance(tweet, dict) or "rest_id" not in tweet:
        return None

    legacy = tweet.get("legacy") or {}
    status_id = str(tweet.get("rest_id") or "")
    text = tweet_text(tweet, legacy)
    created_at_raw = str(legacy.get("created_at") or "")
    if not status_id or not text or not created_at_raw:
        return None

    views = tweet.get("views") or legacy.get("views") or {}
    view_count = views.get("count", 0) if isinstance(views, dict) else 0
    try:
        view_count = int(view_count or 0)
    except (TypeError, ValueError):
        view_count = 0

    return Tweet(
        status_id=status_id,
        text=text,
        created_at_utc=parse_x_datetime(created_at_raw),
        created_at_raw=created_at_raw,
        lang=str(legacy.get("lang") or ""),
        likes=int(legacy.get("favorite_count") or 0),
        retweets=int(legacy.get("retweet_count") or 0),
        replies=int(legacy.get("reply_count") or 0),
        views=view_count,
        raw_json=tweet,
    )


def tweet_text(tweet: dict[str, Any], legacy: dict[str, Any]) -> str:
    note_result = (
        tweet.get("note_tweet", {})
        .get("note_tweet_results", {})
        .get("result", {})
    )
    if isinstance(note_result, dict):
        note_text = note_result.get("text")
        if note_text:
            return normalize_text(str(note_text))
    return normalize_text(str(legacy.get("full_text") or ""))


def normalize_text(text: str) -> str:
    return text.replace("\u2028", "\n").replace("\u2029", "\n")


def user_timeline_instructions(data: dict[str, Any]) -> list[dict[str, Any]]:
    result = data.get("data", {}).get("user", {}).get("result", {})
    timeline = (
        result.get("timeline_v2", {}).get("timeline")
        or result.get("timeline", {}).get("timeline")
        or {}
    )
    return timeline.get("instructions", []) or []


def search_instructions(data: dict[str, Any]) -> list[dict[str, Any]]:
    return (
        data.get("data", {})
        .get("search_by_raw_query", {})
        .get("search_timeline", {})
        .get("timeline", {})
        .get("instructions", [])
        or []
    )


def collect_entries(instructions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for instr in instructions:
        if "entries" in instr:
            entries.extend(instr.get("entries") or [])
        if "entry" in instr:
            entries.append(instr.get("entry") or {})
    return entries


def parse_entry(entry: dict[str, Any]) -> tuple[list[Tweet], str | None]:
    content = entry.get("content") or {}
    if content.get("cursorType") == "Bottom":
        return [], content.get("value") or None

    tweet_results: list[dict[str, Any]] = []
    if content.get("entryType") == "TimelineTimelineItem":
        tweet_results.append(content.get("itemContent", {}).get("tweet_results", {}).get("result", {}))
    elif content.get("entryType") == "TimelineTimelineModule":
        for item in content.get("items") or []:
            candidate = (
                item.get("item", {})
                .get("itemContent", {})
                .get("tweet_results", {})
                .get("result", {})
            )
            if candidate:
                tweet_results.append(candidate)
    tweets = [tweet for result in tweet_results if (tweet := parse_tweet_result(result))]
    return tweets, None
