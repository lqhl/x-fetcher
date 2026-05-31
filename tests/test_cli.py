from datetime import date, datetime, timezone

from x_fetcher.cli import main
from x_fetcher.models import Tweet, UserProfile
from x_fetcher.store import Store


def make_tweet(status_id: str, created_at: str) -> Tweet:
    dt = datetime.fromisoformat(created_at).replace(tzinfo=timezone.utc)
    return Tweet(
        status_id=status_id,
        text=f"tweet {status_id}",
        created_at_utc=dt,
        created_at_raw="Wed Jan 01 12:00:00 +0000 2025",
        raw_json={"id": status_id},
    )


def test_fetch_fast_skips_complete_range_without_secrets(tmp_path, capsys):
    db_path = tmp_path / "x_fetcher.db"
    config_path = tmp_path / "config.yaml"
    secrets_path = tmp_path / "missing-secrets.yaml"
    config_path.write_text(f"db_path: {db_path}\nwindow_days: 7\n", encoding="utf-8")

    store = Store(db_path)
    store.init_schema()
    user_id = store.upsert_user(UserProfile("alice", "100"))
    store.upsert_tweets(user_id, [make_tweet("1", "2025-01-02T00:00:00")])
    store.complete_window(user_id, date(2025, 1, 1), date(2025, 1, 8), [], None)
    store.close()

    code = main(
        [
            "--config",
            str(config_path),
            "--secrets",
            str(secrets_path),
            "fetch",
            "--user",
            "alice",
            "--since",
            "2025-01-01",
            "--until",
            "2025-01-08",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "fetched_windows=0 skipped_windows=1" in captured.out
    assert "validation ok" in captured.out
