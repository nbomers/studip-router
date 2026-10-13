# studip-router

**Sync your Stud.IP course files into *your* folder structure — routed by rules, renamed the way you like, never overwriting anything.**

Most Stud.IP downloaders mirror the course's folder tree. `studip-router` files every document into the folders you already use — whatever you call them (`Exercises`, `Übungen`, `Hausaufgaben`, `Slides`, `Skript`, `Exams/Past papers`, …) — with per-course rules, because every lecturer organises their course differently.

![Home screen](docs/screenshots/menu.svg)

## Features

- **One key to sync**: run `studip`, press `s`, confirm the one-line summary. Done.
- **Incremental**: only new or changed files are downloaded; a second run does nothing.
- **Rule-based routing**, per course, first match wins: Stud.IP folder, file name (glob/regex), extension, MIME type, size, date, description.
- **Rule suggestions** that understand German and English folder names on both sides (`Übungsblätter`, `Homework`, `Tutorium …` → your `Exercises`/`Übungen`/… folder) — including Stud.IP courses with one folder per session.
- **Renaming**: templates with regex groups (`Blatt (\d+)` → `Exercise_{1:02}{ext}`), zero padding, umlauts, spaces, prefixes.
- **Settings screen**: every option on one page, change with ←/→ (or `h`/`l`), saved immediately — comments in your config file are kept.
- **Safe**: never deletes, never silently overwrites, never touches files you edited, atomic downloads, clean Ctrl-C.
- **Knows your existing files**: files you downloaded by hand are recognised (even as `Name_with_underscores.pdf`) instead of being downloaded twice; a file you moved within the course folder is followed.
- **Updates become versions**: a changed file is saved as `name_v2.pdf` next to the old one (configurable).
- **Robust with messy files**: odd characters, very long names, missing extensions, interrupted transfers, HTML error pages instead of files, broken ZIP names, iCloud — see [Tricky files](#tricky-files).
- **Extras**: course announcements, ZIP extraction, merge all slides into one PDF, background sync with desktop notifications, `--json` output, profiles for several accounts.

## Requirements

- Python 3.11+, macOS or Linux (Windows should work but is untested).
- A Stud.IP instance with the **JSON:API enabled** and **username/password login** (local or LDAP accounts). Check: `https://<your-studip>/jsonapi.php/v1/discovery` must open in a browser. SSO-only accounts (Shibboleth/OIDC without a Stud.IP password) are not supported yet.

## Installation

```bash
uv tool install git+https://github.com/nbomers/studip-router
```

or `pipx install git+https://github.com/nbomers/studip-router`. Update with `uv tool upgrade studip-router`.

## Quickstart

```bash
studip
```

The first start opens a short setup: Stud.IP address, username, password (stored in your OS keychain), the folder that contains your course folders. It matches your courses to your folders, then you are on the home screen:

| Key | | Key | |
|---|---|---|---|
| `s` | **Sync now** (summary → confirm) | `r` | Rules: suggestions, test a file name |
| `p` | Preview every file and its target | `o` | Reorganize synced files (`u` undo) |
| `w` | What's new: open / reveal / preview | `m` | Merge slides into one PDF |
| `a` | Announcements | `,` | Settings |
| `c` | Courses: sync on/off, choose folders | `d` | Doctor: check the setup |

`j`/`k` and arrow keys move, `Enter` opens, `Esc` goes back, `?` shows all keys.

Recommended first steps: `r` → add the suggested rules → `p` to check → `s` to sync.

| Preview | Settings |
|---|---|
| ![Sync preview](docs/screenshots/sync-preview.svg) | ![Settings](docs/screenshots/settings.svg) |

Prefer the command line? Everything works without the UI:

```bash
studip init                     # setup
studip rules suggest --write    # add suggested rules
studip sync --dry-run           # see exactly what would happen
studip sync                     # do it
```

## Configuration

The config lives at `~/.config/studip-router/config.toml` (`studip config path`). Most options are in the settings screen; rules are written in the file. See the fully commented **[config.example.toml](config.example.toml)**.

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
# Rule sheets
#   → ~/Uni/Semester 5/Quantum Cooking/Exercises/Exercise_07.pdf
```

Changed your rules after syncing? `studip reorganize --dry-run` shows how synced files would move; `studip reorganize` moves them, `studip undo` moves them back.

### Settings reference

| Key | Default | Meaning |
|---|---|---|
| `sync.semester` | `"current"` | `"current"`, `"all"` or a title like `"WiSe 2026/27"` |
| `sync.on_update` | `"version"` | changed remote file: `version` (`_v2`), `overwrite`, `skip` |
| `sync.on_conflict` | `"suffix"` | two files, same name: `suffix` (`_2`), `skip`, `error` |
| `sync.default_target` | `"Sonstiges"` | folder for files no rule matches (`init` detects yours) |
| `sync.create_missing_dirs` | `false` | create rule target folders that do not exist |
| `sync.unzip` | `false` | extract `.zip` downloads next to the archive |
| `sync.notify` | `true` | notification after background syncs |
| `sync.exclude` | videos, > 200 MB | `globs`, `mime`, `max_size_mb` (`0` = no limit) |
| `rename.pad_numbers` | `2` | `lecture-1` → `lecture-01`; `0` disables |
| `rename.spaces` / `umlauts` / `lowercase` | keep | `_` or `-` / `transliterate` / `true` |
| `rename.portable` | `true` | replace `: * ? " < > \|` (Windows, OneDrive, iCloud) |
| `rename.strip_prefixes` | `[]` | e.g. `["Vorlesung_"]` |
| `ui.theme` | `"tokyo-night"` | terminal UI colours |
| `courses.<id>.exclude` | `false` | never touch this course |
| `courses.<id>.merge` | – | `{ source = "Slides", output = "All slides.pdf" }` |

From scripts: `studip config set sync.on_update skip`.

## Commands

| Command | |
|---|---|
| `studip` | terminal UI |
| `studip sync [--dry-run] [--course X] [--semester S] [--include-large]` | sync |
| `studip courses` | courses with status active / excluded / new |
| `studip status` | new courses, files only placed by the default folder |
| `studip rules list / test / suggest [--write]` | inspect and create rules |
| `studip reorganize` / `studip undo` | apply changed rules to synced files / revert |
| `studip history` / `studip news` | recently synced files / unread announcements |
| `studip merge` | merge slide PDFs |
| `studip schedule install --every 120` / `uninstall` | background sync (opt-in) |
| `studip doctor` | check config, folders, keychain, server, scheduler |
| `studip auth login / status / logout` | password in the OS keychain |
| `studip config path / validate / edit / set` | config file |

Global options: `--profile NAME` (separate config + state, e.g. a second university), `--json` (machine-readable output for scripts, Raycast, Alfred, …).

## Shell integration (fish)

```bash
cp shell/fish/completions/studip.fish ~/.config/fish/completions/
```

Completes subcommands, flags and your course folders. `shell/fish/functions/studip.fish` is an optional wrapper for running from a source checkout.

## Tricky files

| Situation | What happens |
|---|---|
| `:` `*` `?` `"` `<` `>` `\|` in names | replaced (`Kapitel 1: Intro` → `Kapitel 1 - Intro`), so the folder also syncs to Windows/OneDrive/iCloud for Windows (`rename.portable`) |
| names longer than 255 bytes (umlauts count twice) | shortened, extension kept, room left for `_v2` |
| `/`, control characters, leading dot, trailing dots/spaces, `CON`/`NUL`… | made safe |
| `&amp;` & co. in Stud.IP names | decoded |
| file without extension | added from the MIME type (`Blatt 1` + PDF → `Blatt 1.pdf`) |
| upper/lower case, NFC/NFD (`ü` vs `u` + `¨`) | treated as the same name; no duplicates on macOS |
| two files want the same name | `name_2.pdf` (or skip/report) |
| hand-downloaded `Homework_1.pdf` vs `Homework 1.pdf` on Stud.IP | recognised as the same file |
| interrupted download, wrong size | retried; never leaves half files |
| Stud.IP sends a login/error page instead of the file | rejected and reported, retried next time |
| Stud.IP reports a wrong size | accepted once two downloads agree |
| one file fails | the others continue; the failed one is retried next sync |
| files only in iCloud (also old `.name.icloud` placeholders) | never downloaded just to compare |
| ZIPs with garbled umlaut names, ZIP-slip, ZIP bombs | names repaired, unsafe archives refused |
| link to an external site redirects elsewhere | your credentials are never sent to the other host |

## How it stays safe

- **Nothing is ever deleted.** Files removed on Stud.IP are only reported.
- **Nothing is overwritten unnoticed.** Name clashes get a suffix; updates become `_v2`; `overwrite` mode never replaces a file you edited (size/mtime/hash check against the state).
- **Dry run == real run.** Both use the same planner; the real run only executes the plan.
- **Downloads are atomic**: `name.part`, fsync, rename. Ctrl-C leaves no partial files.
- **Your password** lives only in the OS keychain — never in the config, state, logs or output.
- **Config edits** (settings screen, rule suggestions) are validated before writing; the first change keeps a `config.toml.bak`.

## FAQ

**My folders have different names than in the examples.**
That's the point: targets are whatever folders you have. `studip init` and the rule suggestions detect them (German and English, with or without umlauts) and never pick archive folders like `old` or `Archiv`.

**A folder is called `Skript / Alle Folien` in Finder but the tool says it does not exist.**
macOS shows `:` as `/` in Finder. On disk it is `Skript : Alle Folien` — use that in the config. `studip doctor` points this out.

**I already downloaded files by hand. Will they be downloaded twice?**
No. A file with the same name (spaces, `_`, `-` treated alike) and size anywhere in the course folder is recorded instead. A same-named file with different content that you fetched this semester is treated as an older version: the new one goes next to it as `_v2`.

**I deleted a file locally. Will it come back?**
Yes, the next sync downloads it again. Files you never want are excluded with a rule (`skip = true`) or `sync.exclude`.

**My files are in iCloud Drive and "optimised" away.** Fine: evicted files are never downloaded for comparison. Keep the state outside synced folders (the default).

**Can I sync older semesters?** `studip sync --semester "SoSe 2026"`, `--semester all`, or `a` in the courses screen.

**My university uses SSO only.** Not supported yet; contributions welcome (see [docs/DESIGN.md](docs/DESIGN.md)).

## Troubleshooting

| Problem | Fix |
|---|---|
| `authentication failed (HTTP 401)` | `studip auth login` (or `L` in settings); your account needs a Stud.IP password, not SSO-only |
| `JSON:API not available (HTTP 404)` | the JSON:API is disabled at your university; ask the Stud.IP admins |
| `target folder does not exist` | create it, fix the rule target, or turn on *create folders* in the settings |
| `received an HTML page instead of the file` | log in to Stud.IP in the browser and check the file is accessible |
| config errors | `studip config validate` names the exact key, e.g. `courses.<id>.rules[2].match.name_regex` |
| anything else | `studip doctor`, then the log at `~/.local/state/studip-router/studip-router.log` |

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/DESIGN.md](docs/DESIGN.md).

## License

[MIT](LICENSE). Not affiliated with the Stud.IP e.V. or any university.
