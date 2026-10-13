# studip-router – Design

`studip-router` mirrors course files from any Stud.IP instance (via the JSON:API)
into an existing local folder structure, routing and renaming files by
per-course rules. The CLI command is `studip`.

## Goals and invariants

1. **Never delete** local files. Files removed on Stud.IP are only reported.
2. **Never silently overwrite.** Updates follow the configured version policy;
   files the user changed locally (size/mtime/hash differ from state) are never
   touched.
3. **Dry run == real run.** Both execute the same planner; the real run only
   adds the executor step. The plan is the single source of truth.
4. **Idempotent.** A second `studip sync` without remote changes is a no-op:
   no downloads, no renames, no ping-pong.
5. **No secrets at rest outside the OS keychain.** Never in config, state,
   logs or the repository, not even in `--verbose`.
6. **Instance-agnostic.** `base_url` is configuration; nothing is tied to a
   specific university.

## Verified API facts (Stud.IP JSON:API, `/jsonapi.php/v1`)

| Need                  | Route                                                   |
|-----------------------|---------------------------------------------------------|
| Auth check, user id   | `GET users/me` (HTTP Basic Auth)                        |
| Course list           | `GET users/{id}/courses?include=start-semester`         |
| Folder tree           | `GET courses/{id}/folders`                              |
| File metadata         | `GET courses/{id}/file-refs` (paginated)                |
| Download              | `GET file-refs/{id}/content` (`HEAD` also available)    |

File-ref attributes: `name, chdate, mkdate, filesize, mime-type, description,
is-downloadable, is-readable, downloads`. No content hash is provided, so
hashes are computed locally after download.

Basic Auth only works for accounts whose password Stud.IP can verify (local or
LDAP). SSO-only accounts (OIDC/Shibboleth) are out of scope for v1; the auth
layer is pluggable so a session-cookie or token provider can be added later.

## Architecture

```
cli ──► config ──► planner ──► executor ──► report
          │          │  ▲          │
          │          ▼  │          ▼
          │        rules/rename   fsutil (atomic write, NFC, iCloud)
          │          ▲
          └──► api ──┘      state (SQLite) ◄──► planner, executor
```

The **planner** is pure: given config, remote metadata and state, it returns a
list of actions (`Download`, `NewVersion`, `SkipLocalModified`,
`SkipExcluded`, `SkipLarge`, `Conflict`, `RemoteDeleted`, `Mkdir`). Dry run
prints the plan; sync executes it.

### Package layout (`src/` layout)

```
src/studip_router/
  __init__.py
  __main__.py      # python -m studip_router
  cli.py           # typer app: init, courses, sync, status, rules, rename,
                   # reorganize, undo, config, doctor
  config.py        # TOML load + schema validation with precise error paths
  auth.py          # keyring-backed credential provider (pluggable)
  api.py           # HTTP client: pagination, retries/backoff, rate limits,
                   # streaming download
  models.py        # dataclasses: Course, Folder, RemoteFile, Action, ...
  semester.py      # "current" resolution, semester filters
  rules.py         # routing engine (ordered, first match wins)
  rename.py        # rename engine (regex/template/normalisation/padding)
  planner.py       # pure diff: remote x state x config -> actions
  executor.py      # applies actions: .part download, atomic rename, versions
  state.py         # SQLite store + undo journal
  fsutil.py        # NFC normalisation, safe path joining, iCloud dataless
                   # detection, atomic writes
  report.py        # console tables/summary (colour only on TTY), log file
shell/fish/
  functions/studip.fish
  completions/studip.fish
tests/             # pytest, API mocked with respx, no real data
```

### Dependencies (runtime)

| Package       | Why                                                            |
|---------------|----------------------------------------------------------------|
| `httpx`       | Timeouts, streaming downloads, connection pooling, testable with `respx` |
| `typer`       | Subcommands/flags with little code; brings `rich` for TTY-aware tables and colour |
| `platformdirs`| Correct config/state/log dirs per OS                           |
| `keyring`     | macOS Keychain / Secret Service / Windows Credential Locker   |

Stdlib covers the rest: `tomllib`, `sqlite3`, `hashlib`, `logging`,
`unicodedata`. Dev: `pytest`, `respx`, `ruff`.

## Paths, Unicode and iCloud

- All names are normalised to **NFC** before comparison and before writing.
  Existing files are matched by NFC-normalised name so NFD files on disk are
  not duplicated.
- Stud.IP names may contain `/`. On macOS the folder that Finder shows as
  `A / B` is stored as `A : B` on disk. Target paths in config are written as
  on-disk (POSIX) names; `/` always means a path separator. A `/` inside a
  remote file name is replaced with `-` (configurable).
- **iCloud:** evicted ("dataless") files are detected via `st_flags`
  (`SF_DATALESS`). The tool never reads their content (that would trigger a
  download); change detection uses state (size + mtime) instead of hashing.
- Downloads go to `<name>.part` in the target directory, are fsynced, then
  `os.replace`d. Ctrl-C removes the current `.part` file.
- Files ignored locally: `.DS_Store`, `*.part`, `*.tmp`, `*.icloud`.

## State (SQLite, outside any synced folder)

Location: `platformdirs.user_state_dir("studip-router")`.

```
files(file_id PK, course_id, remote_name, remote_folder_path, chdate,
      filesize, local_path, local_size, local_mtime, sha256, version,
      rule_id, routed_by_default, synced_at)
runs(run_id PK, started_at, finished_at, mode, summary_json)
journal(run_id, seq, op, src, dst)        -- for `studip undo`
```

Idempotency: a file is identified by `file_id`, never by its (renamed) local
name. A file is re-downloaded only if `chdate` or `filesize` changed remotely.

## Config schema (TOML)

Location: `platformdirs.user_config_dir("studip-router")/config.toml`.
Courses are keyed by Stud.IP ID (names change, IDs don't). Validation errors
point to the key path, e.g. `courses.<id>.rules[2].match.name_regex: invalid
regex: missing ), unterminated subpattern at position 7`. TOML syntax errors
include line and column.

```toml
[server]
base_url = "https://studip.example.edu"
username = "jdoe"                   # password: keychain only

[sync]
root = "~/Uni/Semester 5"
semester = "current"                # "current" | "WiSe 2026/27" | "all"
only_configured_courses = true      # unknown courses are reported
on_update = "version"               # "version" (_v2) | "overwrite" | "skip"
on_conflict = "suffix"              # "suffix" | "skip" | "error"
default_target = "Sonstiges"
create_missing_dirs = false         # TBD, see open questions

[sync.exclude]                      # global file-level exclusions
globs = ["*.mp4", "*.mkv"]
mime = ["video/*"]
max_size_mb = 200

[rename]                            # global default rename behaviour
pad_numbers = 2                     # lecture-1.pdf -> lecture-01.pdf
spaces = "keep"                     # "keep" | "_" | "-"
umlauts = "keep"                    # "keep" | "transliterate"
lowercase = false
strip_prefixes = []

[courses.0123456789abcdef0123456789abcdef]
name = "Example Course"             # informational
target = "Example Course"           # relative to root, or absolute
default_target = "Sonstiges"        # optional override

  [[courses.0123456789abcdef0123456789abcdef.rules]]
  id = "sheets"
  match = { folder = "Übungszettel*", name_regex = '(?i)blatt[_ ]?(\d+)' }
  target = "Uebungen"
  rename = "Uebung_{1:02}{ext}"

  [[courses.0123456789abcdef0123456789abcdef.rules]]
  id = "slides"
  match = { ext = ["pdf"], name_glob = "*VL*" }
  target = "Skript : Alle Folien"

  [[courses.0123456789abcdef0123456789abcdef.rules]]
  id = "exams"
  match = { name_regex = '(?i)klausur|exam' }
  target = "Klausur/Altklausuren"

[courses.fedcba9876543210fedcba9876543210]
name = "Some Course I Don't Want"
exclude = true
```

### Match criteria (all optional, combined with AND)

`folder` (glob on Stud.IP folder path), `name_glob`, `name_regex`,
`ext`, `mime`, `min_size_mb`/`max_size_mb`, `changed_after`/`changed_before`,
`description_regex`. Globs are case-insensitive by default; regexes use their
own flags (`(?i)`).

### Rename templates

Variables: `{course}`, `{orig_name}`, `{orig_stem}`, `{ext}` (with dot),
`{folder}`, `{date}` (chdate, `YYYY-MM-DD`), `{semester}`, and regex groups
`{1}`, `{2}`, `{name}` with format specs (`{1:02}` zero-pads numeric groups).
After templating, the global normalisation (`pad_numbers`, `spaces`, …) runs.
A rename that produces an empty name or a path separator is a validation
error.

## CLI

```
studip init                      # wizard: login test, keychain, course mapping
studip courses [--all]           # id, semester, status (active/excluded/unconfigured)
studip sync [--course X] [--semester S|--all-semesters] [--dry-run] [-v]
studip status                    # unconfigured courses, default-routed files, errors
studip rules list [COURSE]
studip rules test COURSE FILENAME [--folder F]
studip rename --dry-run          # before/after table for configured renames
studip reorganize [--dry-run]    # apply current rules to already synced files
studip undo                      # revert the last reorganize/sync moves
studip config path|edit|validate
studip doctor                    # API, auth, paths, write perms, iCloud checks
```

## Milestones

| Phase | Content | Done when |
|-------|---------|-----------|
| 2 – MVP | api, auth, config, state, planner/executor without rules, `courses`, `sync [--dry-run]`, exclusions | Real dry run on the semester; second sync is a no-op |
| 3 – Rules | routing, rename, conflicts, version policy, `rules test/list`, default report, `status` | Tests for Unicode, conflicts, idempotency green |
| 4 – Comfort | fish function + completions, `init`, `doctor`, `reorganize`, `undo` | Works in a fresh fish shell |
| 5 – Release | README, `config.example.toml`, CONTRIBUTING, CI (ruff + pytest, Ubuntu + macOS), packaging, secret scan; optional launchd scheduler | A stranger sets it up in < 10 min |

## Open questions

- Should the tool create missing rule target subfolders (`create_missing_dirs`)?
- Auto-sync via launchd (Phase 5, opt-in)?
