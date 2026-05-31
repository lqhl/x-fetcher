from datetime import datetime, timezone
from pathlib import Path

from x_fetcher.fetcher import split_windows
from x_fetcher.models import Tweet, UserProfile
from x_fetcher.store import Store
from x_fetcher.validate import validate_range


def make_tweet(status_id: str, created_at: str, likes: int = 1) -> Tweet:
    dt = datetime.fromisoformat(created_at).replace(tzinfo=timezone.utc)
    return Tweet(
        status_id=status_id,
        text=f"tweet {status_id}",
        created_at_utc=dt,
        created_at_raw="Wed Jan 01 12:00:00 +0000 2025",
        likes=likes,
        raw_json={"id": status_id},
    )


def open_store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "test.db")
    store.init_schema()
    return store


def test_upsert_updates_without_duplicate(tmp_path: Path):
    store = open_store(tmp_path)
    user_id = store.upsert_user(UserProfile("alice", "100"))

    first = store.upsert_tweets(user_id, [make_tweet("1", "2025-01-02T00:00:00", 1)])
    second = store.upsert_tweets(user_id, [make_tweet("1", "2025-01-02T00:00:00", 9)])
    stats = store.stats(user_id)
    row = store.conn.execute("SELECT likes FROM tweets WHERE user_id = ? AND status_id = '1'", (user_id,)).fetchone()

    assert first.inserted == 1
    assert second.updated == 1
    assert stats["count"] == 1
    assert row["likes"] == 9
    store.close()


def test_split_windows():
    from datetime import date

    assert split_windows(date(2025, 1, 1), date(2025, 1, 16), 7) == [
        (date(2025, 1, 1), date(2025, 1, 8)),
        (date(2025, 1, 8), date(2025, 1, 15)),
        (date(2025, 1, 15), date(2025, 1, 16)),
    ]


def test_validate_reports_missing_and_failed_windows(tmp_path: Path):
    from datetime import date

    store = open_store(tmp_path)
    user_id = store.upsert_user(UserProfile("alice", "100"))
    store.upsert_tweets(user_id, [make_tweet("1", "2025-01-02T00:00:00")])
    store.complete_window(user_id, date(2025, 1, 1), date(2025, 1, 8), [], None)
    store.failed_window(user_id, date(2025, 1, 8), date(2025, 1, 15), "boom")

    report = validate_range(store, "alice", date(2025, 1, 1), date(2025, 1, 22), 7)

    assert not report.ok
    assert report.total == 1
    assert report.failed_windows == ["2025-01-08..2025-01-15"]
    assert report.missing_windows == ["2025-01-15..2025-01-22"]
    store.close()


def test_validate_reports_complete_window_with_unexhausted_cursor(tmp_path: Path):
    from datetime import date

    store = open_store(tmp_path)
    user_id = store.upsert_user(UserProfile("alice", "100"))
    store.upsert_tweets(user_id, [make_tweet("1", "2025-01-02T00:00:00")])
    store.complete_window(
        user_id,
        date(2025, 1, 1),
        date(2025, 1, 8),
        [make_tweet("1", "2025-01-02T00:00:00")],
        "cursor-still-present",
        "stopped after 3 consecutive empty pages",
    )

    report = validate_range(store, "alice", date(2025, 1, 1), date(2025, 1, 8), 7)

    assert report.ok
    assert report.suspicious_windows == [
        "2025-01-01..2025-01-08: stopped after 3 consecutive empty pages"
    ]
    store.close()


def test_complete_window_replaces_overlapping_window_state(tmp_path: Path):
    from datetime import date

    store = open_store(tmp_path)
    user_id = store.upsert_user(UserProfile("alice", "100"))
    store.complete_window(user_id, date(2025, 1, 1), date(2025, 1, 8), [], None)
    store.complete_window(user_id, date(2025, 1, 1), date(2025, 1, 9), [], None)

    rows = store.conn.execute(
        """
        SELECT since_date, until_date
        FROM fetch_windows
        WHERE user_id = ?
        ORDER BY since_date, until_date
        """,
        (user_id,),
    ).fetchall()

    assert [(row["since_date"], row["until_date"]) for row in rows] == [("2025-01-01", "2025-01-09")]
    store.close()


def test_complete_window_records_actual_database_count(tmp_path: Path):
    from datetime import date

    store = open_store(tmp_path)
    user_id = store.upsert_user(UserProfile("alice", "100"))
    store.upsert_tweets(
        user_id,
        [
            make_tweet("1", "2025-01-02T00:00:00"),
            make_tweet("2", "2025-01-03T00:00:00"),
        ],
    )

    store.complete_window(user_id, date(2025, 1, 1), date(2025, 1, 8), [make_tweet("1", "2025-01-02T00:00:00")], None)
    row = store.window_status(user_id, date(2025, 1, 1), date(2025, 1, 8))

    assert row is not None
    assert row["tweet_count"] == 2
    store.close()
