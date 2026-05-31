from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from .fetcher import is_skippable_window, split_windows
from .store import Store


@dataclass
class ValidationReport:
    ok: bool
    total: int = 0
    oldest: str | None = None
    newest: str | None = None
    monthly_counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    missing_windows: list[str] = field(default_factory=list)
    partial_windows: list[str] = field(default_factory=list)
    failed_windows: list[str] = field(default_factory=list)
    suspicious_windows: list[str] = field(default_factory=list)


def validate_range(store: Store, screen_name: str, since: date, until: date, window_days: int = 7) -> ValidationReport:
    user_id = store.get_user_id(screen_name)
    if user_id is None:
        return ValidationReport(False, errors=[f"target user @{screen_name} is missing from users"])

    report = ValidationReport(ok=True)
    row = store.conn.execute("SELECT rest_id FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row or not row["rest_id"]:
        report.errors.append("user rest_id is empty")

    dup = store.conn.execute(
        """
        SELECT COUNT(*) total, COUNT(DISTINCT status_id) distinct_count
        FROM tweets WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()
    if int(dup["total"]) != int(dup["distinct_count"]):
        report.errors.append("duplicate status_id rows exist for this user")

    bounds = (
        datetime.combine(since, datetime.min.time(), timezone.utc).isoformat(),
        datetime.combine(until, datetime.min.time(), timezone.utc).isoformat(),
    )
    bad = store.conn.execute(
        """
        SELECT status_id, text, created_at_utc
        FROM tweets
        WHERE user_id = ?
          AND created_at_utc >= ?
          AND created_at_utc < ?
          AND (text = '' OR status_id GLOB '*[^0-9]*')
        LIMIT 10
        """,
        (user_id, *bounds),
    ).fetchall()
    if bad:
        report.errors.append(f"{len(bad)} sampled tweets have invalid date/text/status_id")

    stats = store.stats(user_id, since, until)
    report.total = int(stats["count"])
    report.oldest = stats["oldest"]
    report.newest = stats["newest"]
    report.monthly_counts = stats["months"]  # type: ignore[assignment]

    for win_since, win_until in split_windows(since, until, window_days):
        win = store.window_status(user_id, win_since, win_until)
        label = f"{win_since.isoformat()}..{win_until.isoformat()}"
        if not win:
            if not is_skippable_window(store, user_id, win_since, win_until):
                report.missing_windows.append(label)
        elif win["status"] == "partial":
            report.partial_windows.append(label)
        elif win["status"] == "failed":
            report.failed_windows.append(label)
        elif win["status"] == "complete" and int(win["exhausted_cursor"] or 0) == 0:
            reason = win["completion_reason"] or "unknown completion reason"
            report.suspicious_windows.append(f"{label}: {reason}")

    if report.errors or report.missing_windows or report.partial_windows or report.failed_windows:
        report.ok = False
    return report
