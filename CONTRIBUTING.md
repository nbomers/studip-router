# Contributing

Thanks for helping! Bug reports with `studip doctor` output (remove anything personal) and pull requests are welcome.

## Setup

```bash
git clone https://github.com/nbomers/studip-router && cd studip-router
uv sync                      # creates .venv with dev dependencies
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run studip --help
```

Working inside iCloud Drive/Dropbox? Point the venv elsewhere so it is not synced, e.g. `ln -s .venv.nosync .venv` before `uv sync`.

## Ground rules

- **Never delete user files**, never overwrite without the configured policy, never read iCloud-evicted files implicitly. New features must keep these invariants (see [docs/DESIGN.md](docs/DESIGN.md)).
- **Planner and executor stay separate**: the planner decides, the executor only performs. Dry run must show exactly what a real run does.
- **No real data in the repository**: no real course names, IDs, user names, paths or tokens — not in tests, fixtures, screenshots or commit messages. Tests use the fake Stud.IP in `tests/fake_studip.py`.
- **No secrets in logs**, not even at debug level.
- Tests for every behaviour change (`pytest`, API mocked with `respx`, TUI via Textual's `run_test`).
- Conventional commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`).

## Layout

```
src/studip_router/
  api.py          Stud.IP JSON:API client (pagination, retries, streaming)
  config.py       TOML loading + validation with key paths in errors
  config_edit.py  change single values, keeping comments (tomlkit)
  rules.py        routing rules        rename.py     rename engine
  planner.py      remote x state x disk -> actions (pure, read-only)
  executor.py     performs actions atomically, verifies downloads
  sync.py         course/semester selection, running plans, merges
  state.py        SQLite state         fsutil.py     names, NFC, iCloud, atomic writes
  suggest.py      rule suggestions     postprocess.py  unzip + PDF merge
  reorganize.py   reorganize/undo      news.py, notify.py, schedule.py, doctor.py
  wizard.py       setup helpers        cli.py        typer CLI
  tui/            Textual UI: app.py (home), views.py, settings.py, setup.py, widgets.py
```

Screenshots in the README come from `uv run python scripts/make_screenshots.py` (fake data only).
