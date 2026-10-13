# studip-router

**Sync your Stud.IP course files into *your* folder structure — routed by rules, renamed the way you like, never overwriting anything.**

Most Stud.IP downloaders mirror the course's folder tree. `studip-router` instead files every document into the folders you already use (`Uebungen`, `Skript`, `Klausur/Altklausuren`, …), with per-course rules, because every lecturer organises their course differently.

![Main menu](docs/screenshots/menu.svg)

## Features

- **Incremental sync** via the Stud.IP JSON:API: only new or changed files are downloaded; a second run is a no-op.
- **Rule-based routing**: per course, first match wins. Match on Stud.IP folder, file name (glob/regex), extension, MIME type, size, date, description.
- **Renaming**: templates with regex groups (`Blatt_(\d+)` → `Uebung_{1:02}{ext}`), zero padding, umlaut transliteration, prefix stripping.
- **Safe by design**: never deletes, never silently overwrites, never touches files you edited, atomic downloads (`.part` + rename), clean Ctrl-C.
- **Knows your existing files**: files you downloaded by hand earlier are recognised and recorded instead of being downloaded again.
- **Updates as versions**: a changed file becomes `name_v2.pdf` next to the old one (configurable).
- **Rule suggestions** from Stud.IP folder names and your existing subfolders.
- **Terminal UI** (run `studip`) with sync preview, live progress, "what's new", announcements and more.
- **Extras**: course announcements, ZIP extraction, merging all slides into one PDF, background sync with desktop notifications, `--json` output, multiple profiles.
- **iCloud-aware**: files evicted to iCloud are never pulled down just to compare them; Unicode (NFC/NFD) handled correctly.

## Requirements

- Python 3.11+
- A Stud.IP instance with the **JSON:API enabled** and **username/password login** (local or LDAP accounts). Check with `curl -I https://<your-studip>/jsonapi.php/v1/discovery` — it should return `200`. SSO-only accounts (Shibboleth/OIDC without a Stud.IP password) are not supported yet.
- macOS or Linux (Windows should work but is untested).

## Installation

```bash
uv tool install studip-router
```

or with pipx: `pipx install studip-router`. From source:

```bash
git clone https://github.com/nbomers/studip-router && cd studip-router
uv tool install .
```

## Quickstart (5 minutes)

```bash
studip init                     # login test, course list, target folders -> config
studip rules suggest --write    # optional: derive rules from Stud.IP folder names
studip sync --dry-run           # see exactly what would happen
studip sync                     # do it
```

Or just run `studip` and use the terminal UI.

| Sync preview | After syncing |
|---|---|
| ![Sync preview](docs/screenshots/sync-preview.svg) | ![Sync done](docs/screenshots/sync-done.svg) |

## Configuration

The config lives at `~/.config/studip-router/config.toml` (`studip config path`). See the fully commented **[config.example.toml](config.example.toml)**. The essentials:

```toml
[server]
base_url = "https://studip.example.edu"
username = "jdoe"                         # password: `studip auth login` -> keychain

[sync]
root = "~/Uni/Semester 5"
default_target = "Sonstiges"              # where unmatched files go

[courses.0123456789abcdef0123456789abcdef]   # Stud.IP course ID (`studip courses`)
name = "Quantum Cooking"
target = "Quantum Cooking"

[[courses.0123456789abcdef0123456789abcdef.rules]]
id = "sheets"
match = { folder = "Übungsblätter*", name_regex = 'blatt[ _]?(\d+)' }
target = "Uebungen"
rename = "Uebung_{1:02}{ext}"
```

Check a rule without downloading anything:

```bash
studip rules test quantum "Blatt 7.pdf" --folder "Übungsblätter"
# Rule sheets
#   → ~/Uni/Semester 5/Quantum Cooking/Uebungen/Uebung_07.pdf
```

Changed your rules after syncing? `studip reorganize --dry-run` shows how already-synced files would move; `studip reorganize` moves them, `studip undo` moves them back.

### Settings reference

| Key | Default | Meaning |
|---|---|---|
| `sync.semester` | `"current"` | `"current"`, `"all"` or a title like `"WiSe 2026/27"` |
| `sync.on_update` | `"version"` | changed remote file: `version` (`_v2`), `overwrite`, `skip` |
| `sync.on_conflict` | `"suffix"` | two files, same name: `suffix` (`_2`), `skip`, `error` |
| `sync.default_target` | `"Sonstiges"` | folder for files no rule matches |
| `sync.create_missing_dirs` | `false` | create rule target folders that do not exist |
| `sync.unzip` | `false` | extract `.zip` downloads next to the archive |
| `sync.exclude` | videos, > 200 MB | `globs`, `mime`, `max_size_mb` |
| `rename.*` | pad numbers to 2 | `pad_numbers`, `spaces`, `umlauts`, `lowercase`, `strip_prefixes` |
| `courses.<id>.exclude` | `false` | never touch this course |
| `courses.<id>.merge` | – | `{ source = "...", output = "All slides.pdf" }` |

## Commands

| Command | |
|---|---|
| `studip` | terminal UI |
| `studip sync [--dry-run] [--course X] [--semester S] [--include-large]` | sync |
| `studip courses` | courses with status active / excluded / unconfigured |
| `studip status` | unconfigured courses, files only placed by the default target |
| `studip rules list / test / suggest` | inspect and create rules |
| `studip reorganize` / `studip undo` | apply changed rules to synced files / revert |
| `studip history` / `studip news` | recently synced files / unread announcements |
| `studip merge` | merge slide PDFs |
| `studip schedule install --every 120` | background sync with notifications (opt-in) |
| `studip doctor` | check config, folders, keychain, server, scheduler |
| `studip auth login / status / logout` | password in the OS keychain |
| `studip config path / validate / edit` | config file |

Global options: `--profile NAME` (separate config + state, e.g. a second university), `--json` (machine-readable output for scripts, Raycast, Alfred, …).

## Shell integration (fish)

```bash
cp shell/fish/completions/studip.fish ~/.config/fish/completions/
```

Completes subcommands, flags and your course folders. `shell/fish/functions/studip.fish` is an optional wrapper for running from a source checkout (`set -Ux STUDIP_ROUTER_SRC ~/path/to/studip-router`).

## How it stays safe

- **Nothing is ever deleted.** Files removed on Stud.IP are only reported.
- **Nothing is overwritten unnoticed.** Name clashes get a suffix; updates become `_v2`; `overwrite` mode never replaces a file you edited (size/mtime/hash check against the state).
- **Dry run == real run.** Both use the same planner; the real run only executes the plan.
- **Downloads are atomic**: written to `name.part`, fsynced, then renamed. Ctrl-C leaves no partial files.
- **Your password** is stored only in the OS keychain — never in the config, state, logs or output.
- **State** (`~/.local/state/studip-router/state.sqlite`) remembers file IDs, so renamed files are not downloaded again.

## FAQ

**My folder is called `Skript / Alle Folien` in Finder but the tool says it does not exist.**
macOS shows `:` as `/` in Finder. On disk the folder is `Skript : Alle Folien` — use that in the config. `studip doctor` points this out.

**I already downloaded some files by hand. Will they be downloaded twice?**
No. Files with the same name (spaces and underscores are treated alike, as Stud.IP replaces spaces in browser downloads) and the same size anywhere in the course folder are adopted. A same-named file with different content that you fetched this semester is treated as an older version: the new one is saved as `_v2` next to it.

**My files are in iCloud Drive / are "optimised" away.**
Fine. Evicted files are recognised and never downloaded just for comparison. Keep the state outside synced folders (the default).

**Can I sync older semesters?** `studip sync --semester "SoSe 2026"` or `--semester all`.

**My university uses SSO only.** Not supported yet; a pluggable auth provider (session cookie / token) is planned. Contributions welcome.

## Troubleshooting

| Problem | Fix |
|---|---|
| `authentication failed (HTTP 401)` | `studip auth login`; check that your account has a Stud.IP password (not SSO-only) |
| `JSON:API not available (HTTP 404)` | your instance has the JSON:API disabled; ask the Stud.IP admins |
| `target folder does not exist` | create it, fix the rule target, or set `create_missing_dirs = true` |
| config errors | `studip config validate` names the exact key, e.g. `courses.<id>.rules[2].match.name_regex` |
| anything else | `studip doctor`, then the log at `~/.local/state/studip-router/studip-router.log` |

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/DESIGN.md](docs/DESIGN.md).

## License

[MIT](LICENSE). Not affiliated with the Stud.IP e.V. or any university.
