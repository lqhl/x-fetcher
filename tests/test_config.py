from pathlib import Path

from x_fetcher.config import load_config


def test_load_config_cli_overrides(tmp_path: Path):
    config = tmp_path / "config.yaml"
    secrets = tmp_path / "secrets.yaml"
    config.write_text(
        """
targets:
  - screen_name: olduser
    since: "2024-01-01"
    until: "2024-02-01"
db_path: old.db
window_days: 14
""",
        encoding="utf-8",
    )
    secrets.write_text("auth_token: aaa\nct0: bbb\n", encoding="utf-8")

    loaded = load_config(
        config,
        secrets,
        user="newuser",
        since="2025-01-01",
        until="2025-02-01",
        db_path="new.db",
        max_tweets=123,
    )

    assert loaded.target_for().screen_name == "newuser"
    assert loaded.target_for().since.isoformat() == "2025-01-01"
    assert loaded.target_for().until.isoformat() == "2025-02-01"
    assert loaded.db_path == Path("new.db")
    assert loaded.max_tweets == 123
    assert loaded.window_days == 14
    assert loaded.secrets and loaded.secrets.auth_token == "aaa"
