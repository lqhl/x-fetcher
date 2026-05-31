from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class UserProfile:
    screen_name: str
    rest_id: str
    display_name: str = ""
    profile_created_at: str = ""


@dataclass(frozen=True)
class Tweet:
    status_id: str
    text: str
    created_at_utc: datetime
    created_at_raw: str
    lang: str = ""
    likes: int = 0
    retweets: int = 0
    replies: int = 0
    views: int = 0
    raw_json: dict[str, Any] | None = None
