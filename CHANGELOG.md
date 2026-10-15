# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.2] - 2026-10-15

### Security
- Text from Stud.IP is shown as text: titles, file and folder names and announcements
  were read as terminal markup, so an announcement with `[/b]` crashed `studip news`
  and the announcement view, a file name could crash the sync preview, and
  `[link=URL]text[/link]` showed a clickable link whose visible text could pretend
  to be the university's address.
- A download far bigger than announced (more than 10 % or 1 MB) or than the size
  limit (`sync.exclude.max_size_mb`) is stopped while it arrives; before, it was
  written in full and even accepted once two transfers agreed.
- Pagination stops after 1000 pages or when the server repeats a page, and every
  record is used once (no `name_2.pdf` copies of the same file).
- Redirects to plain `http://` are refused (only `https`, or `http` to localhost).
- Bidi control characters are removed from file names.

### Fixed
- The lowest supported typer version is 0.13 (0.12 could not start any command);
  CI now also tests the lowest allowed versions of all dependencies.

### Changed
- Switching on `overwrite` for changed files, or creating missing rule folders, asks
  for confirmation in the settings screen.
- Automatic routing and rule suggestions treat Stud.IP folders named like
  `Notebooks` / `notebook-03` (Jupyter notebooks) as exercises: their files go to the
  course's exercise folder (`Übungen`, `Exercises`, …) instead of the default folder.
  Your own rules still win; without an exercise folder nothing changes.

## [0.2.1] - 2026-10-15

### Security
- The terminal UI no longer prints the password when it crashes: crash reports are
  rendered without local variables, and the two realistic causes are now ordinary
  errors: an HTML page instead of JSON (SSO login, maintenance page) and a missing
  OS keychain.
- A rejected login is never repeated: `news`, `rules suggest`, `ignore add`,
  `dates`, Courseware and downloads stop at the first 401 (a sync also pauses
  auto-sync), so a wrong password cannot lock the account.
- `sync.root` and course targets refuse `/`, the home folder itself and system
  folders; template fields like `{orig_name.__class__}` are rejected.
- ZIP extraction: 1 GiB limit, compression-ratio check, drive letters refused, member
  names made safe, case-only clashes kept apart.
- State database, log and lock are created readable only by the user.
- The terminal UI does not open programs and scripts with `o` (downloads carry no
  quarantine flag); the scheduler always runs this installation's `studip`.
- Dependencies get upper bounds; CI actions are pinned to commit SHAs; Dependabot.

### Fixed
- `on_update = "overwrite"` re-checks for local edits right before replacing a file,
  so an edit made while the preview was open is kept.
- A local `Blatt_01.pdf` of the same size was adopted as the Stud.IP file `Blatt.pdf`;
  `name_vN` now only counts as a version when `name` exists next to it.
- One unexpected error (e.g. a broken PDF) failed the whole sync instead of one file.
- Automatic routing sent `Examples`, `Cheat sheets`, `Scripts`/`Code` folders to
  exams, exercises or slides.
- The setup proposed last semester's folder `Analysis I` for `Analysis II`.
- Extracting an archive removed an existing `<name>.unzip-tmp` folder.
- Unlocking PDFs never rewrites a PDF you edited since it was synced.
- Dates and announcements without a UTC offset no longer break the comparison.
- A double Enter no longer confirms a dialog before it could be read.
- A damaged state database or one from a newer version gives a clear message
  (exit code 4); the schema is versioned. Renames are persisted with a directory
  fsync; merged PDFs are written atomically.

### Added
- Releases are published to PyPI (`uv tool install studip-router`), built and
  uploaded by GitHub Actions with Trusted Publishing.
- `studip doctor` checks the keychain, lists course targets outside the root and does
  not test the login while auto-sync is paused.
- README: exit codes, uninstall, terms of use, privacy, headless use; `mypy` in CI.

## [0.2.0] - 2026-10-15

### Added
- Setup in three steps with good guesses: it finds the folder that already holds
  most of your course folders (iCloud Drive, Documents, Desktop, Dropbox, OneDrive,
  Google Drive, Nextcloud) or proposes `iCloud Drive/Uni/<semester>`; new course
  folders get the subfolders your existing ones share (default
  `Folien, Übungen, Klausur, Sonstiges`, `sync.course_folders`); the same in `studip init`.
- Settings: *course folders in*, *new course gets* and *default folder* are edited
  with Enter; README section "Defaults and how to change them".
- Automatic routing: a file that no rule matches goes to the local folder its Stud.IP
  folder name points to (`Übungen`, `Tutorium`, `Folien`, `Slides`, `Vorlesung`, …)
  instead of the default folder; rules always win; `sync.auto_route` turns it off.
- A sync (summary, `--json`), `studip status` and the home screen point out synced
  files that changed rules or automatic routing would now put elsewhere
  (`studip reorganize`); files moved by hand are not counted.
- `studip init` offers to create missing course folders, like the setup in the UI;
  the courses screen (`c`) can create a folder for a new course (*+ new folder*).
- `sync.exclude.folders` (globs on Stud.IP folder names); by default the
  `CoursewareImport*` folders Stud.IP creates when Courseware is imported are skipped.

### Changed
- The default folder (`Sonstiges`) is created when a file needs it, instead of every
  file of a course failing with "target folder does not exist"; rule targets still
  need `create_missing_dirs`.

## [0.1.0] - 2026-10-14

First public version.

### Added
- Incremental sync via the Stud.IP JSON:API with Basic Auth; password in the OS keychain.
- Per-course routing rules (folder, name glob/regex, extension, MIME type, size, date,
  description) and a rename engine (templates, regex groups, zero padding, umlauts,
  spaces, prefixes, portable names).
- Rule suggestions for German/English folder names on both sides, session folders,
  nested Stud.IP folders.
- Recognition of hand-downloaded files, updates as `_v2` versions, restore of locally
  deleted files, following of moved files.
- Terminal UI: home dashboard, one-key sync, preview, what's new, announcements,
  courses, rules, reorganize/undo, slide merging, Monkeytype-style settings, setup
  wizard, doctor.
- CLI with `--json` and `--profile`; `init`, `sync`, `status`, `rules`, `reorganize`,
  `undo`, `history`, `news`, `merge`, `schedule`, `doctor`, `auth`, `config`.
- Background sync via launchd/systemd with desktop notifications (opt-in).
- ZIP extraction and merging of slide PDFs.
- Robust file handling: portable and length-limited names, extensions from MIME
  types, verified downloads with retries, HTML error page detection, iCloud
  placeholders, repaired ZIP member names.
- fish completions.
- Hardening after comparing with studip-sync, PFERD and Moodle-DL: per-course error
  isolation, defensive API parsing, no duplicate versions for renames/moves/re-uploads,
  rename following, instant-based date comparison, semester-change handling,
  per-profile run lock, unique temp files, one-time deletion reports, ignore list,
  back-off for failing files, failure notifications and login pause for background
  sync, `folder_type` rules, OS certificate store, opt-in Courseware files,
  standalone-only number padding, recovery of versions after state loss.
- Course dates: after each sync, cancelled, moved and re-roomed dates and new exam dates
  are reported (summary, notification, `--json`); `studip dates` and `t` in the UI list
  recent changes and upcoming exams; `sync.watch_dates` turns it off.
- The Stud.IP address must use `https://` (plain `http://` only for `localhost`), so the
  password is never sent unencrypted.
- `studip pdf-password set COURSE`: one shared PDF password per course, kept in the
  keychain; PDFs locked with it are stored without it (also those synced before).
  Other files, unprotected PDFs and PDFs with a different password stay untouched.

[0.2.0]: https://github.com/nbomers/studip-router/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/nbomers/studip-router/releases/tag/v0.1.0

[Unreleased]: https://github.com/nbomers/studip-router/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/nbomers/studip-router/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/nbomers/studip-router/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/nbomers/studip-router/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/nbomers/studip-router/releases/tag/v0.1.0
