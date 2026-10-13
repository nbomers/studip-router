# studip-router – Design

`studip-router` mirrors course files from any Stud.IP instance (JSON:API) into
an existing local folder structure, routing and renaming files by per-course
rules. The command is `studip`; without arguments it opens a terminal UI.

## Invariants

1. **Never delete** local files. Files removed on Stud.IP are only reported.
   (`undo` only moves files back and removes *empty* folders it created.)
2. **Never silently overwrite.** Updates follow `on_update`; files the user
   changed locally (size/mtime/hash differ from the state) are never replaced.
3. **Dry run == real run.** One planner produces the actions; the executor
   only performs them.
4. **Idempotent.** Without remote changes a second sync does nothing: files are
   identified by Stud.IP file ID, never by their (renamed) local name.
5. **No secrets at rest outside the OS keychain**, never in logs or output.
6. **iCloud-safe.** Evicted ("dataless") files are never read implicitly; only
   `stat()` is used for them.
7. **Instance-agnostic.** Nothing university-specific is hard-coded.

## Stud.IP JSON:API usage

| Need                 | Route                                                |
|----------------------|------------------------------------------------------|
| Auth check, user id  | `GET users/me` (HTTP Basic Auth)                     |
| Semesters            | `GET semesters`                                      |
| Courses              | `GET users/{id}/courses`                             |
| Folder tree          | `GET courses/{id}/folders`                           |
| File metadata        | `GET courses/{id}/file-refs` (paginated)             |
| Download             | `GET file-refs/{id}/content` (streamed)              |
| Announcements        | `GET courses/{id}/news`                              |

File refs carry `name, chdate, mkdate, filesize, mime-type, description,
is-downloadable`. There is no content hash; hashes are computed locally.
Basic Auth requires an account Stud.IP can verify itself (local/LDAP);
SSO-only accounts need a future auth provider (session cookie or token).

## Architecture

```
cli.py / tui/   ──► sync.py ──► planner.py ──► executor.py ──► postprocess.py
        │             │   ▲          │  ▲              │
        ▼             ▼   │          ▼  │              ▼
     config.py      api.py│      rules.py rename.py   fsutil.py (atomic, NFC, iCloud)
                          └──────── state.py (SQLite) ◄─┘
```

- **planner** (pure apart from read-only disk access): remote files × state ×
  disk → actions `download | adopt | new-version | overwrite | mkdir | skip |
  error`, plus "remote deleted" reports.
- **executor**: `.part` download → fsync → `os.replace`, sets mtime to the
  Stud.IP change date, records the state per file, extracts archives.
- **sync**: semester/course selection, running plans, slide merges.
- **reorganize**: re-applies rules to tracked files with a journal for `undo`.

### Key decisions

- *Adoption*: an untracked local file with the same name (case-insensitive,
  NFC, spaces == underscores) and size anywhere in the course folder is
  recorded instead of downloaded. Same name, different size and modified
  since the semester started → treated as an older version (`_v2` next to it).
- *Locally deleted files* are downloaded again to their old path (the folder
  mirrors Stud.IP); a tracked file found elsewhere in the course folder with
  the same name and size is treated as moved and its new path is recorded.
- *Versions*: `name_v2.pdf`, `name_v3.pdf`, counting from the base name.
- *Conflicts*: `name_2.pdf` (or skip/error).
- *Config writes* (`rules suggest --write`, TUI): append-only, validated
  before writing, backup as `config.toml.bak`; comments are preserved.
- *Profiles*: `profiles/<name>.toml` + `state-<name>.sqlite`.
- *Scheduler*: launchd agent / systemd user timer, opt-in only.

## Paths

| What   | Default |
|--------|---------|
| config | `~/.config/studip-router/config.toml` |
| state  | `~/.local/state/studip-router/state.sqlite` |
| log    | `~/.local/state/studip-router/studip-router.log` (rotating) |

## State schema

```
files(file_id PK, course_id, remote_name, remote_folder_path, chdate, filesize,
      local_path, local_size, local_mtime, sha256, version, rule_id,
      routed_by_default, synced_at, mime_type, description)
runs(run_id PK, started_at, finished_at, mode, summary)
journal(run_id, seq, op, src, dst)            -- reorganize/undo, downloads
generated(path PK, sha256, fingerprint, updated_at)   -- merged PDFs
news_seen(news_id PK, course_id, seen_at)
```

## Possible future work

- Auth providers for SSO-only instances (session cookie, personal tokens).
- Courseware and Opencast recordings (needs research per instance).
- A small macOS menu bar app on top of `studip --json`.
