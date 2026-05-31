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
