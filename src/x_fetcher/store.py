from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import Tweet, UserProfile


@dataclass(frozen=True)
class UpsertStats:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0

    def __add__(self, other: "UpsertStats") -> "UpsertStats":
        return UpsertStats(
            self.inserted + other.inserted,
            self.updated + other.updated,
            self.skipped + other.skipped,
        )


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                screen_name TEXT NOT NULL UNIQUE,
                rest_id TEXT NOT NULL,
                display_name TEXT,
                profile_created_at TEXT,
                fetched_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tweets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                status_id TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                created_at_raw TEXT NOT NULL,
                lang TEXT,
                likes INTEGER DEFAULT 0,
                retweets INTEGER DEFAULT 0,
                replies INTEGER DEFAULT 0,
                views INTEGER DEFAULT 0,
                raw_json TEXT,
                fetched_at TEXT NOT NULL,
                UNIQUE(user_id, status_id)
            );

            CREATE TABLE IF NOT EXISTS fetch_windows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                since_date TEXT NOT NULL,
                until_date TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'partial', 'complete', 'failed')),
                tweet_count INTEGER DEFAULT 0,
                oldest_seen_at TEXT,
                newest_seen_at TEXT,
                last_cursor TEXT,
                finished_at TEXT,
                error TEXT,
                UNIQUE(user_id, since_date, until_date)
            );

            CREATE INDEX IF NOT EXISTS idx_tweets_user_created ON tweets(user_id, created_at_utc);
            CREATE INDEX IF NOT EXISTS idx_tweets_status ON tweets(status_id);
            CREATE INDEX IF NOT EXISTS idx_windows_user_range ON fetch_windows(user_id, since_date, until_date);
            """
        )
        self.conn.commit()

    def upsert_user(self, profile: UserProfile) -> int:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO users(screen_name, rest_id, display_name, profile_created_at, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(screen_name) DO UPDATE SET
                rest_id=excluded.rest_id,
                display_name=excluded.display_name,
                profile_created_at=excluded.profile_created_at,
                fetched_at=excluded.fetched_at
            """,
            (profile.screen_name.lower(), profile.rest_id, profile.display_name, profile.profile_created_at, now),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT id FROM users WHERE screen_name = ?", (profile.screen_name.lower(),)).fetchone()
        if row is None:
            raise RuntimeError("failed to upsert user")
        return int(row["id"])

    def get_user_id(self, screen_name: str) -> int | None:
        row = self.conn.execute("SELECT id FROM users WHERE screen_name = ?", (screen_name.lower(),)).fetchone()
        return int(row["id"]) if row else None

    def has_tweet(self, user_id: int, status_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM tweets WHERE user_id = ? AND status_id = ?",
            (user_id, status_id),
        ).fetchone()
        return row is not None

    def upsert_tweets(self, user_id: int, tweets: Iterable[Tweet]) -> UpsertStats:
        inserted = updated = skipped = 0
        for tweet in tweets:
            existing = self.conn.execute(
                "SELECT id FROM tweets WHERE user_id = ? AND status_id = ?",
                (user_id, tweet.status_id),
            ).fetchone()
            now = utc_now()
            raw_json = json.dumps(tweet.raw_json or {}, ensure_ascii=False)
            self.conn.execute(
                """
                INSERT INTO tweets(
                    user_id, status_id, text, created_at_utc, created_at_raw, lang,
                    likes, retweets, replies, views, raw_json, fetched_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, status_id) DO UPDATE SET
                    text=excluded.text,
                    created_at_utc=excluded.created_at_utc,
                    created_at_raw=excluded.created_at_raw,
                    lang=excluded.lang,
                    likes=excluded.likes,
                    retweets=excluded.retweets,
                    replies=excluded.replies,
                    views=excluded.views,
                    raw_json=excluded.raw_json,
                    fetched_at=excluded.fetched_at
                """,
                (
                    user_id,
                    tweet.status_id,
                    tweet.text,
                    tweet.created_at_utc.isoformat(),
                    tweet.created_at_raw,
                    tweet.lang,
                    tweet.likes,
                    tweet.retweets,
                    tweet.replies,
                    tweet.views,
                    raw_json,
                    now,
                ),
            )
            if existing:
                updated += 1
            else:
                inserted += 1
        self.conn.commit()
        return UpsertStats(inserted, updated, skipped)

    def complete_window(self, user_id: int, since: date, until: date, tweets: list[Tweet], last_cursor: str | None) -> None:
        oldest = min((t.created_at_utc.isoformat() for t in tweets), default=None)
        newest = max((t.created_at_utc.isoformat() for t in tweets), default=None)
        self._upsert_window(user_id, since, until, "complete", len(tweets), oldest, newest, last_cursor, None)

    def partial_window(
        self,
        user_id: int,
        since: date,
        until: date,
        tweets: list[Tweet],
        last_cursor: str | None,
        error: str | None = None,
    ) -> None:
        oldest = min((t.created_at_utc.isoformat() for t in tweets), default=None)
        newest = max((t.created_at_utc.isoformat() for t in tweets), default=None)
        self._upsert_window(user_id, since, until, "partial", len(tweets), oldest, newest, last_cursor, error)

    def failed_window(self, user_id: int, since: date, until: date, error: str) -> None:
        self._upsert_window(user_id, since, until, "failed", 0, None, None, None, error)

    def _upsert_window(
        self,
        user_id: int,
        since: date,
        until: date,
        status: str,
        tweet_count: int,
        oldest: str | None,
        newest: str | None,
        cursor: str | None,
        error: str | None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO fetch_windows(
                user_id, since_date, until_date, status, tweet_count, oldest_seen_at,
                newest_seen_at, last_cursor, finished_at, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, since_date, until_date) DO UPDATE SET
                status=excluded.status,
                tweet_count=excluded.tweet_count,
                oldest_seen_at=excluded.oldest_seen_at,
                newest_seen_at=excluded.newest_seen_at,
                last_cursor=excluded.last_cursor,
                finished_at=excluded.finished_at,
                error=excluded.error
            """,
            (
                user_id,
                since.isoformat(),
                until.isoformat(),
                status,
                tweet_count,
                oldest,
                newest,
                cursor,
                utc_now() if status in ("complete", "failed") else None,
                error,
            ),
        )
        self.conn.commit()

    def window_status(self, user_id: int, since: date, until: date) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT * FROM fetch_windows
            WHERE user_id = ? AND since_date = ? AND until_date = ?
            """,
            (user_id, since.isoformat(), until.isoformat()),
        ).fetchone()

    def window_has_tweets(self, user_id: int, since: date, until: date) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM tweets
            WHERE user_id = ?
              AND created_at_utc >= ?
              AND created_at_utc < ?
            LIMIT 1
            """,
            (
                user_id,
                datetime.combine(since, datetime.min.time(), timezone.utc).isoformat(),
                datetime.combine(until, datetime.min.time(), timezone.utc).isoformat(),
            ),
        ).fetchone()
        return row is not None

    def stats(self, user_id: int | None = None) -> dict[str, object]:
        where = "WHERE user_id = ?" if user_id else ""
        args = (user_id,) if user_id else ()
        row = self.conn.execute(
            f"SELECT COUNT(*) c, MIN(created_at_utc) oldest, MAX(created_at_utc) newest FROM tweets {where}",
            args,
        ).fetchone()
        months = self.conn.execute(
            f"""
            SELECT substr(created_at_utc, 1, 7) month, COUNT(*) count
            FROM tweets {where}
            GROUP BY month
            ORDER BY month
            """,
            args,
        ).fetchall()
        return {
            "count": int(row["c"]),
            "oldest": row["oldest"],
            "newest": row["newest"],
            "months": {r["month"]: int(r["count"]) for r in months},
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
