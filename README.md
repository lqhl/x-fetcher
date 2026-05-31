# x-fetcher

`x-fetcher` fetches public tweets from X with an authenticated browser-cookie session and stores them in a single multi-user SQLite database. It is designed for repeatable research ingestion: date-windowed fetching, SQLite upserts, resumable window state, and post-fetch coverage validation.

The current implementation uses:

- `UserTweets` for a short recent timeline probe.
- `SearchTimeline` for historical backfill in configurable date windows.
- `fetch_windows` metadata to skip completed work on repeated runs.
- `UNIQUE(user_id, status_id)` plus upserts to prevent duplicate tweet rows.

## Project Layout

```text
src/x_fetcher/
  cli.py        # init/fetch/validate/stats commands
  config.py     # YAML config + secrets loading and CLI overrides
  fetcher.py    # UserTweets/SearchTimeline orchestration
  parser.py     # X GraphQL timeline/tweet parsing
  store.py      # SQLite schema, upserts, window state
  validate.py   # structure and coverage checks
  x_client.py   # cookie-authenticated X web/API client
tests/          # offline parser/store/config/fetcher tests
```

## Setup

```bash
uv sync
cp config.example.yaml config.yaml
cp secrets.example.yaml secrets.yaml
```

Edit `secrets.yaml` with `auth_token` and `ct0` from an active X browser session.

`secrets.yaml`, `config.yaml`, `data/`, `*.db`, `.venv/`, and test caches are ignored by git. Commit `config.example.yaml` for shared defaults; keep real cookies and local run parameters out of commits.

## Commands

```bash
uv run x-fetcher init
uv run x-fetcher fetch --user aleabitoreddit --since 2025-01-01 --until 2026-06-01
uv run x-fetcher validate --user aleabitoreddit --since 2025-01-01 --until 2026-06-01
uv run x-fetcher stats --user aleabitoreddit
```

`until` is exclusive. If omitted, it defaults to tomorrow's UTC date.

## Configuration

`config.yaml` is non-secret but local. The example contains the supported keys:

```yaml
targets:
  - screen_name: aleabitoreddit
    since: "2025-01-01"
    until: null
db_path: data/x_fetcher.db
window_days: 7
user_tweets_max_pages: 10
request_sleep:
  user_tweets: [2.5, 5.5]
  search: [4.0, 8.0]
  window: [20.0, 45.0]
```

CLI flags override config values:

```bash
uv run x-fetcher fetch \
  --user aleabitoreddit \
  --since 2025-01-01 \
  --until 2026-06-01 \
  --db data/x_fetcher.db
```

## Storage Model

The SQLite database contains:

- `users`: screen name, X `rest_id`, display name, profile creation time, last fetched time.
- `tweets`: tweet text, UTC creation time, engagement counts, raw tweet JSON, fetched time.
- `fetch_windows`: per-user date windows with status, counts, cursor, completion time, and errors.

`tweets` has `UNIQUE(user_id, status_id)`. Re-fetching an existing tweet updates mutable fields such as likes, retweets, replies, views, raw JSON, and fetched timestamp instead of inserting a duplicate.

## Incremental Fetching

`fetch` splits the target date range into `window_days` windows. Complete windows are skipped on later runs unless `--force-window` is used. If the whole requested range is already covered by complete windows, `fetch` exits after local validation without making X network requests. Partial or failed windows can be resumed from their saved cursor when available.

The recent `UserTweets` phase stops early when it sees only known tweets for enough pages or reaches `user_tweets_max_pages`. Historical coverage is driven by `SearchTimeline` windows.

## Validation

`fetch` runs validation automatically unless `--no-validate` is passed. You can also run it directly:

```bash
uv run x-fetcher validate --user aleabitoreddit --since 2025-01-01 --until 2026-06-01
```

Validation checks:

- target user exists and has a non-empty `rest_id`
- no duplicate `status_id` values for the user
- tweets are inside `[since, until)`
- tweet text is non-empty and status IDs are numeric strings
- every target date window is complete
- monthly distribution, oldest tweet, newest tweet

## Rate Limits And Safety

The tool is intentionally single-threaded. It does not rotate accounts, bypass risk controls, or fetch users concurrently.

Default behavior:

- randomized sleeps between pages and windows
- 429 exponential backoff with jitter
- shorter 503 retry backoff
- immediate stop on 401/403 so cookies can be refreshed

If a run exits or is interrupted, re-run the same command. Completed windows are skipped and remaining windows continue.

## Development

```bash
uv sync
uv run pytest
```

Before committing, check that no local data or secrets are staged:

```bash
git status --short --ignored
git diff --cached --name-only
```
