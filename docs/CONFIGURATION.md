# Configuration reference

[Back to the quick start](../README.md)

## Defaults and how to change them

| What | Default | Change it |
|---|---|---|
| Folder with your course folders | found by the setup | settings (`,`) → *course folders in* · `sync.root` |
| Subfolders of a new course folder | your existing structure, else `Folien, Übungen, Klausur, Sonstiges` | settings → *new course gets* · `sync.course_folders` |
| Files nothing places | `Sonstiges` (created when needed) | settings → *default folder* · `sync.default_target` |
| Sorting by Stud.IP folder name, then file name (`Übungen` → your exercise folder, …) | on | settings → *auto-route* · `sync.auto_route` |
| Your own routing and renaming | none needed | `r` (suggestions, test a name) · `+` on a file in the preview · `[[courses.<id>.rules]]` |
| New course mid-semester | listed as *new* | `c` → pick a folder or *+ new folder* |
| Videos, files > 200 MB, Courseware import images | skipped | settings → *skip videos*, *size limit* · `sync.exclude` |
| Changed file on Stud.IP | kept next to the old one as `_v2` | settings → *changed files* · `sync.on_update` |
| File names | as on Stud.IP, numbers padded (`1` → `01`) | settings → *names* · `[rename]` |
| Reports changed course dates | on | settings → *watch dates* · `sync.watch_dates` |
| Background sync | off | settings → *auto-sync* · `studip schedule install` |

In the settings screen `h`/`l` switches options and **Enter edits a text value**;
every change is saved at once. The config file (`e` in the settings,
`studip config edit`) has the same keys, with comments kept.

---


## Where files go

```text
~/Uni/Semester 5/                  sync.root — you pick it in the setup
├── Quantum Cooking/               one folder per course (its target)
│   ├── Exercises/                 by a rule, or automatically from a Stud.IP
│   ├── Slides/                    folder name like "Übungen" or "Folien", else the file name
│   └── Sonstiges/                 everything else (sync.default_target)
└── Underwater Basket Weaving/
```

| Folder | Where it comes from | Created automatically? |
|---|---|---|
| **Root** (`sync.root`) | asked in the setup; files go into the course folders inside it (unless you give a course an absolute target). `/`, your home folder itself and system folders are refused | the setup in the UI expects an existing folder; `studip init` creates it together with the course folders |
| **Course folder** | the setup matches each course to an existing folder in the root | the setup (UI and `studip init`) offers to create missing ones; for a new course choose *+ new folder* under `c`. A course folder that disappears later is an error (renamed or moved?), never silently recreated |
| **Default folder** (`Sonstiges`) | files that no rule, folder name or file name places | **yes**, the first time a file needs it |
| **Rule targets** | `target` of your rules | no: a missing one is an error, so a typo never creates folders. `sync.create_missing_dirs = true` creates them |
| **Automatic targets** | your existing folders whose names match (`Übungen`, `Folien`, …) | never needed: only existing folders are used |

Courses you never set up are not synced, unless you set
`sync.only_configured_courses = false`: then each gets a folder named like the
course, created automatically.

Config, state and log stay **outside** your course folders (also on macOS):
`~/.config/studip-router/config.toml` and `~/.local/state/studip-router/`.

---


## Configuration

The config lives at `~/.config/studip-router/config.toml` (`studip config path`).
Most options are in the settings screen; rules are written in the file. The fully
commented reference is **[config.example.toml](https://github.com/nbomers/studip-router/blob/main/config.example.toml)**.

```toml
[server]
base_url = "https://studip.example.edu"
username = "jdoe"                         # password: OS keychain only

[sync]
root = "~/Uni/Semester 5"
default_target = "Misc"                   # where files go that no rule matches

[courses.0123456789abcdef0123456789abcdef]   # Stud.IP course ID (`studip courses`)
name = "Quantum Cooking"
target = "Quantum Cooking"

[[courses.0123456789abcdef0123456789abcdef.rules]]
id = "sheets"
match = { folder = "Übungsblätter*", name_regex = '(?:blatt|sheet)[ _]?(\d+)' }
target = "Exercises"
rename = "Exercise_{1:02}{ext}"
```

Test a rule without downloading anything (also in the UI under `r`):

```bash
studip rules test quantum "Blatt 7.pdf" --folder "Übungsblätter"
```

```text
Rule sheets
  → ~/Uni/Semester 5/Quantum Cooking/Exercises/Exercise_07.pdf
```

> [!TIP]
> **Changed your rules after syncing?** A sync never moves files that are already
> there; it tells you when some are no longer where your rules put them (also on
> the home screen). `studip reorganize --dry-run` shows how they would move,
> `studip reorganize` moves them, `studip undo` moves them back.

<details>
<summary><b>Settings reference</b></summary>

| Key | Default | Meaning |
|---|---|---|
| `sync.semester` | `"current"` | `"current"`, `"all"` or a title like `"WiSe 2026/27"` |
| `sync.on_update` | `"version"` | changed remote file: `version` (`_v2`), `overwrite`, `skip` |
| `sync.on_conflict` | `"suffix"` | two files, same name: `suffix` (`_2`), `skip`, `error` |
| `sync.default_target` | `"Sonstiges"` | folder for files no rule matches (`init` detects yours) |
| `sync.course_folders` | your structure / `Folien, Übungen, Klausur, Sonstiges` | subfolders of every course folder the setup or `c` creates |
| `sync.create_missing_dirs` | `false` | create rule target folders that do not exist (the default folder is always created) |
| `sync.unzip` | `false` | extract `.zip` downloads into a folder named like them and remove the archive (fetched again only when it changes) |
| `sync.notify` | `true` | notification after background syncs |
| `sync.auto_route` | `true` | no rule matches: use the Stud.IP folder name (Übung, Folien, Literatur, …), a homework folder, then the file name before the default folder |
| `sync.keywords` | none | your own words per category for that sorting, e.g. `exercises = ["Werkstatt"]` (categories: `exams`, `solutions`, `exercises`, `slides`, `reading`) |
| `sync.watch_dates` | `true` | report cancelled, moved and re-roomed dates after a sync |
| `sync.follow_renames` | `true` | rename the local copy when a file is renamed on Stud.IP |
| `sync.courseware` | `false` | also sync files from Courseware pages (per course: `courses.<id>.courseware`) |
| `sync.only_configured_courses` | `true` | `false`: sync every course into a folder named like it |
| `sync.exclude` | videos, > 200 MB, `CoursewareImport*` folders | `globs`, `mime`, `max_size_mb` (`0` = no limit), `folders` |
| `rename.pad_numbers` | `2` | `lecture-1` → `lecture-01`; `0` disables |
| `rename.spaces` / `umlauts` / `lowercase` | keep | `_` or `-` / `transliterate` / `true` |
| `rename.portable` | `true` | replace `: * ? " < > \|` (Windows, OneDrive, iCloud) |
| `rename.strip_prefixes` | `[]` | e.g. `["Vorlesung_"]` |
| `ui.theme` | `"tokyo-night"` | terminal UI colours |
| `ui.update_check` | `false` | ask pypi.org once a week for a newer version (UI and `studip status`, never the background sync) |
| `courses.<id>.exclude` | `false` | never touch this course (archived courses get `true`) |
| `courses.<id>.default_target` / `unzip` / `courseware` | the `sync.` value | per-course override |
| `courses.<id>.exclude_files` | none | added to `sync.exclude`; its `max_size_mb` replaces the limit |
| `courses.<id>.rename` | `[rename]` | the keys given replace those of `[rename]` for this course |
| `courses.<id>.merge` | – | `{ source = "Slides", output = "All slides.pdf" }` — or in the UI: Merge slides (`m`), Enter on a course |

From scripts: `studip config set sync.on_update skip`.

</details>

---


## Command reference

| Command | |
|---|---|
| `studip` / `studip tui` | terminal UI |
| `studip init [--force]` | setup in the terminal (login, courses, folders, config) |
| `studip sync [--dry-run] [--course X] [--semester S] [--include-large] [-v] [-q] [--notify]` | sync; `-v` also lists skips, `-q` only problems |
| `studip courses [--semester S]` | courses with status active / excluded / new |
| `studip semester [--dry-run] [--yes] [--keep-root] [--read-only]` | semester change: new folders, new courses, archive old ones |
| `studip status [--offline]` | new courses, files only placed by the default folder, last run |
| `studip rules list / test / suggest [--write]` | inspect and create rules |
| `studip reorganize` / `studip undo` | apply changed rules to synced files / revert |
| `studip history [--limit 30]` | recently synced files |
| `studip news [--course X] [--all] [--keep-unread]` | unread announcements (`--all`: read ones too) |
| `studip dates [--offline] [--days 14] [--ics FILE]` | changed course dates and upcoming exams; `--ics` writes all dates as a calendar file |
| `studip open COURSE` | open a course folder in Finder / the file manager |
| `studip completion zsh` | print the shell completion script (zsh, bash, fish) |
| `studip config remove-course COURSE` | remove a course you left on Stud.IP from the config (files stay) |
| `studip merge [--course X] [--force]` | merge slide PDFs (`--force` downloads iCloud-only PDFs) |
| `studip schedule install --every 120` / `status` / `run-now` / `uninstall` | background sync (opt-in) |
| `studip ignore add / list / remove` | files you never want |
| `studip pdf-password set / list / remove COURSE` | unlock a course's password-protected PDFs |
| `studip doctor [--test-notification]` | check config, folders, keychain, server, scheduler |
| `studip auth login / status / logout` | password in the OS keychain |
| `studip config path / validate / edit / set` | config file |

**Global options:** `--profile NAME` (separate config + state, e.g. a second
university), `--json` (machine-readable output for scripts, Raycast, Alfred, …).

---


## Exit codes and JSON

For scripts: `0` success, `1` some files or courses failed (or files the plan
cannot place, such as a missing target folder), `2` invalid config or
arguments, `3` login or keychain problem, `4` damaged state database, `75` another
run is in progress, `130` interrupted. With `--json`, errors are printed as
`{"error": "..."}`; the other fields of `--json` output may still grow, but existing
ones are kept.

