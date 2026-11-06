# studip-router – Design

`studip-router` mirrors course files from any Stud.IP instance (JSON:API) into
an existing local folder structure, routing and renaming files by per-course
rules. The command is `studip`; without arguments it opens a terminal UI.

## Invariants

1. **Never delete** local files. Files removed on Stud.IP are only reported.
   (`undo` only moves files back and removes *empty* folders it created.)
2. **Never silently overwrite.** Updates follow `on_update`; files the user
   changed locally (size/mtime/hash differ from the state) are never replaced;
   the check is repeated at execution time, right before `os.replace`, because a
   plan can be old (the UI preview stays open).
3. **Dry run == real run.** One planner produces the actions; the executor
   only performs them.
4. **Idempotent.** Without remote changes a second sync does nothing: files are
   identified by Stud.IP file ID, never by their (renamed) local name.
5. **No secrets at rest outside the OS keychain**, never in logs or output, and
   never in crash output (the TUI renders tracebacks without locals; non-JSON
   answers and keychain failures are ordinary errors, not crashes).
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
| Course dates         | `GET courses/{id}/events` (`is-cancelled`, `categories`, `location`) |
| Courseware (opt-in)  | `GET courses/{id}/courseware-units`, structural elements with `include=containers.blocks`, `GET file-refs/{id}` |

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
- **semester**: finds a semester change (new courses, courses that ended) and
  applies it in one config write; see *Semester change* below.
- **Around the sync**: `dates.py` (date changes, exams) and `ics.py` (calendar
  export), `news.py` (announcements), `notify.py` (desktop notifications),
  `schedule.py` (launchd/systemd), `doctor.py` (setup checks),
  `update_hold.py` (the check after an update), `new_version.py` (the opt-in
  PyPI check), `sorting_report.py` (the text for a "sorted wrong" report).

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
- *Not new content*: a changed date with the same content id (Stud.IP's
  `file` relationship) is a rename/move/re-description → `refresh` or local
  `rename`. Without a content id the executor hashes the download and drops it
  if identical. Dates are compared as instants.
- *Re-uploads*: a new file id whose name matches a file deleted in the same
  run is treated as the next version of it.
- *Isolation*: each course is planned in its own try block; malformed API
  records are skipped (`client.warnings`). The executor isolates every file,
  whatever the exception, except `AuthFailed`: a rejected login ends the run at
  once (one attempt, not one per file or course). Files failing 3x are retried daily.
- *Adoption*: an untracked local file with the remote name and size is adopted.
  `name_vN` counts as a version of `name` only when `name` exists next to it (a
  version chain written before the state was lost).
- *Semesters*: configured courses always sync (unless `exclude = true`); the
  semester only decides which unconfigured courses are announced (current + next
  within 60 days).
- *Semester change*: new courses get a folder with the shared subfolders; an
  optional new root (`Semester 5` → `Semester 6`) is a sibling of the old one,
  and then the targets of the existing courses are written out as absolute paths.
  Archiving a course sets `exclude = true` (files stay; `--read-only` removes the
  write bits, folders excepted). Courses you chose to keep are remembered in
  `<profile>.semester-kept.json`, so you are not asked again.
- *After an update*: when a new version would route already synced files to
  other folders, the background sync holds (`<profile>.update-hold`) until the
  UI or a manual `studip sync` has run, at most three days, with one
  notification. `<profile>.seen-version` records the version you last looked at.
- *Update check* (opt-in, `[ui] update_check`): once a week a GET of
  `pypi.org/pypi/studip-router/json`, cached in `version-check.json`; never from
  the background sync. The only host besides Stud.IP the tool ever contacts.
- *Writes*: every file reaches the course folders through `fsutil.publish`
  (downloads, merged PDFs, unlocked PDFs, moves): an existing file is never replaced
  unless the caller passes `may_replace`, a check run right before the replace
  (overwrite: unchanged since we wrote it; merge and unlock: same hash as before).
  Without hard links, the name is claimed with `O_EXCL` before the rename.
- *Unzip retries*: a failure from the file system (`OSError`) is stored with the
  prefix `unzip failed, retrying:`; the planner then plans `unzip` for the unchanged
  archive (back-off as for downloads). Broken or encrypted archives are not retried.
- *Concurrency*: one `flock` per profile around every writing run; temp files
  are `name.<random>.part`, stale ones are removed after 24 h.
- *Background*: a 401 writes `<profile>.auth-paused`; scheduled runs stop until
  a successful login; problems are notified.
- *Courseware* (opt-in): units → structural elements with
  `include=containers.blocks` → file ids from block payloads → `/file-refs/{id}`.
- *Config writes* (`rules suggest --write`, the TUI, `config set`,
  `config remove-course`, `semester`): through tomlkit, so comments are kept;
  validated before writing; the first change of a session keeps
  `config.toml.bak`. Rule suggestions only append; `config remove-course`
  removes a course table and its rules; the semester change rewrites course
  targets in one write.
- *Profiles*: `profiles/<name>.toml` + `state-<name>.sqlite`.
- *Scheduler*: launchd agent / systemd user timer, opt-in only.

## Paths

| What   | Default |
|--------|---------|
| config | `~/.config/studip-router/config.toml` |
| state  | `~/.local/state/studip-router/state.sqlite` |
| log    | `~/.local/state/studip-router/studip-router.log` (rotating) |
| profile | config `profiles/<name>.toml`, state `state-<name>.sqlite` |

Small files next to the state (`<profile>` is `default` without `--profile`):

| File | Purpose |
|------|---------|
| `<profile>.lock` | one writing run at a time (`flock`) |
| `<profile>.auth-paused` | background sync paused after a rejected login |
| `<profile>.semester-kept.json` | courses kept at a semester change (not asked again) |
| `<profile>.seen-version` | the version whose sorting you last looked at |
| `<profile>.update-hold` | background sync waits after an update |
| `version-check.json` | cached answer of the opt-in PyPI check (all profiles) |
| `course-colors.json` / `course-colors-<name>.json` | course colours in the UI |

## State schema

`PRAGMA user_version` holds `SCHEMA_VERSION` (`state.py`, currently 2). Older
files are migrated on open (new columns are added); a newer one is refused with
"written by a newer studip-router", so a downgrade cannot misread it. Bump it with
every schema change and note the change next to the constant.

```
files(file_id PK, course_id, remote_name, remote_folder_path, chdate, filesize,
      local_path, local_size, local_mtime, sha256, version, rule_id,
      routed_by_default, synced_at, mime_type, description, content_id, gone,
      local_sha256, folder_type)              -- local_sha256: unlocked PDF, else NULL
ignored(file_id PK, course_id, name, since)   -- never download (`i`, `studip ignore`)
failures(file_id PK, count, last_error, last_try)   -- back-off for failing files
runs(run_id PK, started_at, finished_at, mode, summary)
journal(run_id, seq, op, src, dst)            -- reorganize/undo, downloads
generated(path PK, sha256, fingerprint, updated_at)   -- merged PDFs
course_dates(date_id PK, course_id, course, start, end, category, location,
             cancelled, description)      -- snapshot of the last date check
date_changes(id PK, course_id, date_id, kind, start, text, detected_at)
news_seen(news_id PK, course_id, seen_at)
```

## Possible future work

- Auth providers for SSO-only instances (session cookie, personal tokens).
- Opencast recordings (separate plugin API).
- A small macOS menu bar app on top of `studip --json`.
