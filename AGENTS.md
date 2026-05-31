# AGENTS.md

## Project Purpose

`x-fetcher` is a Python CLI for authenticated X tweet ingestion into SQLite. It supports multi-user storage, date-windowed historical fetching, duplicate-safe upserts, resumable fetch windows, and validation reports.

## Local Context

This project was created for research workflows where X posts are source material for later quantitative or investment-research analysis. Treat the data ingestion layer as infrastructure: correctness, resumability, explicit failure modes, and avoiding accidental secret/data commits matter more than clever abstractions.

## Safety Rules

- Never commit `secrets.yaml`, `data/`, `*.db`, `.venv/`, `.pytest_cache/`, or `__pycache__/`.
- Do not print or persist real X cookies outside `secrets.yaml`.
- Do not add account rotation, concurrent user fetching, or risk-control bypass logic.
- Keep fetching single-threaded unless the user explicitly changes the product requirement.
- On 401/403, stop and tell the user to refresh cookies.
- On 429/503, preserve progress and use bounded backoff.

## Implementation Notes

- Prefer `uv run ...` for commands.
- Use `rg` for repository search.
- Use `apply_patch` for manual edits.
- Keep config loading in `src/x_fetcher/config.py`.
- Keep X request logic in `src/x_fetcher/x_client.py`.
- Keep GraphQL parsing in `src/x_fetcher/parser.py`.
- Keep SQLite schema/upsert/window behavior in `src/x_fetcher/store.py`.
- Keep orchestration and retry behavior in `src/x_fetcher/fetcher.py`.
- Keep CLI presentation thin in `src/x_fetcher/cli.py`.

## Validation Expectations

Run tests after source changes:

```bash
uv run pytest
```

For live ingestion changes, also run a narrow smoke test with valid local cookies:

```bash
uv run x-fetcher init
uv run x-fetcher fetch --user aleabitoreddit --since 2026-05-01 --until 2026-06-01
uv run x-fetcher validate --user aleabitoreddit --since 2026-05-01 --until 2026-06-01
```

Use the system proxy environment when this machine cannot resolve or reach X directly:

```bash
HTTPS_PROXY=http://127.0.0.1:7890 \
HTTP_PROXY=http://127.0.0.1:7890 \
ALL_PROXY=socks5h://127.0.0.1:7890 \
uv run x-fetcher fetch --user aleabitoreddit --since 2026-05-01 --until 2026-06-01
```

## Commit Hygiene

Before committing:

```bash
git status --short --ignored
git diff --cached --name-only
```

Only commit source, tests, documentation, examples, and lockfiles. Do not commit local runtime config, fetched data, generated caches, virtualenvs, or secrets.
