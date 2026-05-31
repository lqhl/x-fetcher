from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_PATH = Path("config.yaml")
DEFAULT_SECRETS_PATH = Path("secrets.yaml")


@dataclass(frozen=True)
class TargetConfig:
    screen_name: str
    since: date
    until: date


@dataclass(frozen=True)
class SleepConfig:
    user_tweets: tuple[float, float] = (2.5, 5.5)
    search: tuple[float, float] = (4.0, 8.0)
    window: tuple[float, float] = (20.0, 45.0)


@dataclass(frozen=True)
class BackoffConfig:
    rate_limit_seconds: tuple[int, ...] = (300, 600, 1200, 2400)
    service_unavailable_seconds: tuple[int, int] = (60, 180)
    max_consecutive: int = 4


@dataclass(frozen=True)
class SecretsConfig:
    auth_token: str
    ct0: str


@dataclass(frozen=True)
class AppConfig:
    targets: tuple[TargetConfig, ...]
    db_path: Path = Path("data/x_fetcher.db")
    max_tweets: int | None = None
    window_days: int = 7
    user_tweets_stop_known_pages: int = 3
    user_tweets_max_pages: int = 10
    request_sleep: SleepConfig = field(default_factory=SleepConfig)
    backoff: BackoffConfig = field(default_factory=BackoffConfig)
    secrets: SecretsConfig | None = None

    def target_for(self, screen_name: str | None = None) -> TargetConfig:
        if screen_name:
            normalized = screen_name.lstrip("@").lower()
            for target in self.targets:
                if target.screen_name.lower() == normalized:
                    return target
            return TargetConfig(normalized, default_since(), default_until())
        if not self.targets:
            raise ValueError("No targets configured. Add targets to config.yaml or pass --user.")
        return self.targets[0]


def default_since() -> date:
    return date(2025, 1, 1)


def default_until() -> date:
    return datetime.now(timezone.utc).date() + timedelta(days=1)


def parse_date(value: str | date | None, *, default: date | None = None) -> date:
    if value is None:
        if default is None:
            raise ValueError("date value is required")
        return default
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def load_yaml(path: Path, *, required: bool = False) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Missing required file: {path}")
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def _tuple2(value: Any, default: tuple[float, float]) -> tuple[float, float]:
    if value is None:
        return default
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"expected a two-item range, got {value!r}")
    return (float(value[0]), float(value[1]))


def _parse_targets(raw_targets: Any) -> tuple[TargetConfig, ...]:
    if not raw_targets:
        return ()
    if not isinstance(raw_targets, list):
        raise ValueError("targets must be a list")
    parsed: list[TargetConfig] = []
    for item in raw_targets:
        if not isinstance(item, dict):
            raise ValueError("each target must be a mapping")
        screen_name = str(item.get("screen_name") or item.get("user") or "").lstrip("@")
        if not screen_name:
            raise ValueError("target screen_name is required")
        parsed.append(
            TargetConfig(
                screen_name=screen_name,
                since=parse_date(item.get("since"), default=default_since()),
                until=parse_date(item.get("until"), default=default_until()),
            )
        )
    return tuple(parsed)


def load_config(
    config_path: Path = DEFAULT_CONFIG_PATH,
    secrets_path: Path = DEFAULT_SECRETS_PATH,
    *,
    user: str | None = None,
    since: str | None = None,
    until: str | None = None,
    db_path: str | None = None,
    max_tweets: int | None = None,
    require_secrets: bool = False,
) -> AppConfig:
    raw = load_yaml(config_path)
    secrets_raw = load_yaml(secrets_path, required=require_secrets)

    sleep_raw = raw.get("request_sleep") or {}
    backoff_raw = raw.get("backoff") or {}
    sleep = SleepConfig(
        user_tweets=_tuple2(sleep_raw.get("user_tweets"), SleepConfig().user_tweets),
        search=_tuple2(sleep_raw.get("search"), SleepConfig().search),
        window=_tuple2(sleep_raw.get("window"), SleepConfig().window),
    )
    backoff = BackoffConfig(
        rate_limit_seconds=tuple(int(x) for x in backoff_raw.get("rate_limit_seconds", BackoffConfig().rate_limit_seconds)),
        service_unavailable_seconds=tuple(
            int(x) for x in backoff_raw.get("service_unavailable_seconds", BackoffConfig().service_unavailable_seconds)
        ),
        max_consecutive=int(backoff_raw.get("max_consecutive", BackoffConfig().max_consecutive)),
    )

    targets = _parse_targets(raw.get("targets"))
    if user:
        base = None
        normalized = user.lstrip("@")
        for target in targets:
            if target.screen_name.lower() == normalized.lower():
                base = target
                break
        targets = (
            TargetConfig(
                screen_name=normalized,
                since=parse_date(since, default=base.since if base else default_since()),
                until=parse_date(until, default=base.until if base else default_until()),
            ),
        )
    elif since or until:
        if not targets:
            raise ValueError("--since/--until require --user when config has no targets")
        first = targets[0]
        targets = (
            TargetConfig(
                first.screen_name,
                parse_date(since, default=first.since),
                parse_date(until, default=first.until),
            ),
            *targets[1:],
        )

    secrets = None
    if secrets_raw:
        auth_token = str(secrets_raw.get("auth_token") or "")
        ct0 = str(secrets_raw.get("ct0") or "")
        if not auth_token or not ct0:
            raise ValueError("secrets.yaml must contain auth_token and ct0")
        secrets = SecretsConfig(auth_token=auth_token, ct0=ct0)

    return AppConfig(
        targets=targets,
        db_path=Path(db_path or raw.get("db_path") or "data/x_fetcher.db"),
        max_tweets=max_tweets if max_tweets is not None else raw.get("max_tweets"),
        window_days=int(raw.get("window_days", 7)),
        user_tweets_stop_known_pages=int(raw.get("user_tweets_stop_known_pages", 3)),
        user_tweets_max_pages=int(raw.get("user_tweets_max_pages", 10)),
        request_sleep=sleep,
        backoff=backoff,
        secrets=secrets,
    )
