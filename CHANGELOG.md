# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.5.0] - 2026-11-06

### Audit follow-up
- Validate blank login fields, malformed course tables and finite size limits;
  resolve relative sync roots against the config file and pin background job paths.
- Share local/remote/exam vocabulary, handle deep folders and false friends,
  and support custom target terms, quizzes and flashcards.
- Fit dialogs to small terminals; add full path details, clipboard copy, preview
  filtering/pagination, a narrow announcements layout, custom size limits and
  settings undo. Display every rule condition and editable simulation metadata.
- Serialize and flush private config updates; refuse detected external edits.
  Keep concurrent merge/PDF edits and remove the unsafe no-clobber rename fallback.
- Bound JSON bodies, retry interrupted reads, reject foreign direct API origins,
  and check target path length/containment. Ask before opening unknown file types.
- Update pypdf to 6.21.4 and certifi to 2026.10.23, raise vulnerable dependency
  lower bounds and audit runtime advisories in CI.
- Compare date changes by actual instants across the repeated daylight-saving hour.
- Split CLI/TUI workflow modules and user documentation; add storage failure,
  recovery, keyboard/resize tests and an offline 1k/10k/50k-file benchmark.

### Added
- **Semester change:** `n` in the UI (shown on the home screen when a new
  semester's courses appear or a course has ended) and `studip semester`. Creates
  a folder for the new semester when the root is named after one (`Semester 5` →
  `Semester 6`), a folder per new course with the subfolders your courses share,
  and archives courses that have ended (no longer synced, files stay). Old courses
  keep their folders; their targets become absolute. `--read-only` also makes the
  files of archived courses read-only. One config write, with a `.bak` copy.
- **A rule from a file:** `+` in the preview on a file adds a rule for every file in
  its Stud.IP folder, or for every file named like it (`Blatt_03.pdf` →
  `Blatt_*.pdf`), into a folder you pick or create.
- **Update check (opt-in):** *update check* in the settings (`[ui] update_check`)
  asks pypi.org once a week whether a newer version exists and shows it on the
  home screen and in `studip status`. Off by default; the background sync never
  asks. `SECURITY.md` names it as the only exception to "only Stud.IP is contacted".
- **Report a file sorted wrong:** `b` in the preview writes a short report (Stud.IP
  folder, file name, what placed it, where it went; no course data) that you edit
  and then copy or open as a new GitHub issue (prefilled) in the browser. Nothing is sent by
  the tool.
- `scripts/homebrew_formula.py` writes a Homebrew formula for a released version
  (for a tap; not tested on a Mac yet).
- **Check after an update:** if a new version would put files you already have into
  other folders, the background sync waits until you have looked (the UI, or a
  `studip sync` by hand), at most three days, and sends one notification.
  `studip doctor` shows the wait.

### Fixed
- Preserve dollar signs in systemd config/state paths. Scheduler status and doctor
  query the actual Linux timer state; command failures are reported as CLI errors.
- An archive whose extraction failed for a passing reason (disk full, permissions,
  only in iCloud) is extracted again on the next sync, from the file on disk; a
  broken or encrypted archive is reported once and left until it changes.

### Changed
- Dialogs for typed text are centred like the other dialogs.
- `b` (report a file sorted wrong) only reports files the automatic sorting or the
  default folder placed; for a file placed by your own rule it points to the rule.
- `studip sync --dry-run -v` lists three or more skipped files of one Stud.IP
  folder with the same reason as one line (`Folder/ (45 files)`), and prints the
  course folder above each table in one piece, so the path can be copied.
- Internal: every file reaches the course folders through `fsutil.publish`, which
  never replaces an existing file unless the caller passes a check that runs right
  before the replace (`may_replace`). Safe is the default; the merge bug above came
  from the old default, which replaced.
- Internal: the module for the check after an update is `update_hold.py` (was
  `update_check.py`, easily confused with the `[ui] update_check` setting, which
  belongs to `new_version.py`).
- Docs: `docs/DESIGN.md` describes the semester change, the update hold, all state
  tables and the small files next to the state; the README lists every command
  option and per-course setting; `config.example.toml` no longer mentions Windows.

### Security
- pypdf 6.21.4 or newer is required (it reads PDFs from Stud.IP); the versions
  locked before had 14 published advisories. certifi updated as well.
- A weekly `pip-audit` of the locked dependencies (`.github/workflows/audit.yml`),
  also on every change to `uv.lock` or `pyproject.toml`.
- A crafted ZIP (folders nested thousands of levels deep, or encrypted) no longer
  stops the whole sync with `sync.unzip = true`: archives with more than 32 folder
  levels are refused, folder entries count towards the 20 000 entry limit, and any
  extraction error fails only that archive.
- Merging slides never replaces an output file that was saved or edited while the
  merge ran (checked again right before the replace), nor a new output file that
  appeared meanwhile. An output only in iCloud is kept rather than replaced unseen;
  `--force` only permits downloading inputs.
- Before a file is overwritten (`on_update = "overwrite"`) or unlocked, its content
  hash is compared too: an edit that keeps size and time is no longer missed.
- Use exclusive rename operations when hard links are unavailable; never fall back
  to a rename that can replace a file created by another application.
- Disable CLI traceback locals on all supported Typer versions, pause background
  sync after rejected authentication during news/date checks, and accept only OS
  keychain backends for passwords.
- Keep configuration files private when appending suggested rules.

### Fixed
- Partially blocked undo operations keep their unfinished steps available.
- PDF passwords are scoped to the server, account and profile. Existing course
  passwords must be entered again with `studip pdf-password set COURSE`; ambiguous
  legacy entries are no longer read or automatically migrated.
- Setup preserves leading and trailing spaces in passwords.

## [0.4.2] - 2026-11-05

### Added
- `studip dates --ics FILE` writes all known course dates as a calendar file.
- `[sync.keywords]`: your own words per category for automatic sorting.
- `studip open COURSE` opens a course folder.
- Courses you left (or that were deleted) on Stud.IP can be removed from the config:
  `x` in Courses (they are listed as "✗ gone") or `studip config remove-course`.
  Files already synced stay; the config keeps a `.bak` copy.
- `studip completion zsh|bash|fish` prints the completion script; it now ships with
  the package, so installs from PyPI have it too. Adds zsh and bash completions.
- Announcements: `x` marks all as read. `?` opens the help on every screen.
- The sync preview and `--json` (`missing_courses`) name configured courses that are
  no longer on Stud.IP (deleted, or you left them).
- Course colours for light themes; one colour file per profile.
- Setup explains a failed login: JSON:API not enabled, or address not reachable.

### Security
- Terminal control characters (escape sequences) in titles, file names and
  announcements from Stud.IP are removed before anything is shown or logged; they
  could fake links or clear the screen.
- A new download never replaces a file of the same name that appeared while it was
  downloading; that file is kept and the download is reported as failed.
- A course titled `~` (with `only_configured_courses = false`) no longer syncs into
  the home folder.
- Linux notifications: course titles can no longer pass options to `notify-send` or
  be read as markup.
- `STUDIP_ROUTER_PROFILE` is checked like `--profile`.
- The state folder is private before the scheduler writes its log into it; the
  course colours are written atomically.

### Fixed
- Esc right after a ZIP download no longer leaves the archive packed for good.
- Failed files move the progress bar, and Esc works even when every file fails.
- A Courseware unit closed to you (HTTP 403/404) no longer closes the whole course.
- Reorganize knows the Stud.IP folder type and no longer moves files from homework
  folders to the default folder; names whose rename template uses `{semester}` stay.
- `studip sync` exits with 1 (and the notification counts a problem) for files the
  plan cannot place, e.g. "target folder does not exist".
- Rule suggestions "by file name" are only offered without automatic sorting, follow
  the classifier's order (a solution sheet is no longer an exercise) and are not
  offered again once added.
- After a sync, Enter in the sync screen loads a fresh plan instead of running the
  finished one again (which reported every file as failed); "Try again" on the home
  screen only counts files that are still wanted.
- The settings no longer crash after the config file became invalid.
- Moving through announcements no longer reloads everything from Stud.IP each time.
- Esc in the setup goes one step back; on the first step it asks before quitting.
- `?` opens the help on every screen; opening Merge slides no longer merges (`m` does).
- The focused settings row has its coloured background again.
- Retry-After values below 0 or "nan" no longer abort a run.
- Course colours are fetched even when the announcements of a course fail.
- Keywords: plurals of short ones (Labs, HWs), more names that only contain a keyword
  (Spickzettel, Titelblatt, Klausuranmeldung, ...), "Klausur/Alt" is a target for old
  exams again.
- Home screen: one status fetch at a time, "Stud.IP not reachable" instead of
  silence, and the cursor stays on the chosen action when the status arrives.
- Announcements are read when you move past them, not when the screen opens.
- The config file is written readable only by you.
- Files synced before this version get their Stud.IP folder type on the next sync,
  so Reorganize can sort them like new files.
- Leaving the UI while the home screen still loads its status no longer triggers a
  "coroutine was never awaited" warning.
- When the config turns invalid in the settings, the UI goes back to the home
  screen (which shows the problem), also when the settings were opened from another
  screen.

### Changed
- State database schema 2 (new column `folder_type`, added automatically). Going
  back to 0.4.1 afterwards stops with "written by a newer studip-router".
- `--json` and `-p/--profile` also work after the command (`studip sync --json`).
- `studip history` takes `-l` for `--limit`; `-n` (dry run everywhere else) still
  works but prints a note.
- Keys inside screens no longer reuse the home screen's letters: Ctrl+R lists again
  in the sync screen, `f` shows a file in its folder in "What's new" (Enter opens
  it). The old keys `r` and `o` still work there until 1.0.

## [0.4.1] - 2026-11-05

### Changed
- Course colours come from Stud.IP: the colour group of each course in "My courses"
  (the colours of your timetable). Courses without one get distinct colours in the
  order of the config, so no two courses share a colour any more.

## [0.4.0] - 2026-11-05

### Added
- Merge slides is set up in the UI: Enter on a course picks its slides folder (slide
  folders first), `m` merges now; no config editing needed.
- A progress bar wherever the UI waits for Stud.IP (sync preview course by course,
  courses, announcements, dates, rule suggestions, merging, doctor, setup login).
- Each course has its own colour, the same on every screen.
- Settings: the essentials on top, the rest under *advanced* (opens in place).
  `,` opens the settings from every screen, `+` shows more actions on the home screen.

### Fixed
- A course whose announcements, dates or files are closed to you (HTTP 403/404) no
  longer stops announcements, rule suggestions or the home screen for all others;
  it is skipped and named.
- Files operating systems leave behind (`.DS_Store`, `Thumbs.db`, `desktop.ini`,
  `._*`) are no longer downloaded from Stud.IP.

### Changed
- Much longer keyword lists for automatic routing, checked against 130 real folder
  and file names: Zentral-/Präsenz-/Haus-/Globalübung, Ü 3, HW1, Einsendeaufgaben,
  Recitations, Problem sets, Mitschrift, Tafelbild, Zusammenfassung,
  Formelsammlung, Lehrbrief/Kurseinheit (FernUni), Nachklausur, Midterm, Mock exam,
  Erwartungshorizont, Semesterapparat, Papers, Texte, Quellen and more; umlauts
  written without "e" (Ubung, Losung, Prufung). "_", "-", "." count as spaces.
  Jupyter notebooks (.ipynb) count as exercises.
- Names that only contain a keyword are no longer taken for it: Merkblatt,
  Deckblatt, Infoblatt, Datenblatt/Datasheet, Laufzettel, Prüfungsordnung,
  Vorlesungsverzeichnis/-plan, Handbuch, Raumbuchung.
- What's new has a coloured course column and shows the folder inside the course.
- Doctor: course folders that are fine are one line, paths are shortened
  ("iCloud Drive/…"), a missing course folder says how to fix it.
- Shorter menu texts, help screen and hints; the selected row is highlighted.

## [0.3.0] - 2026-11-05

### Changed
- Automatic routing also reads the file name when the Stud.IP folder name says
  nothing: "Uebungsblatt_3.pdf" in the course root or in "Allgemeiner Dateiordner"
  goes to your exercise folder instead of the default folder. Stud.IP homework
  folders count as exercises; "Labor" is an exercise folder; "Literatur"/"Reader"
  folders go to a local literature folder if you have one. A folder name still
  wins over the file name, and your rules over both. Files already synced stay
  where they are; `studip reorganize` offers to move them.
- Home screen: one suggested next step (Enter does it), five everyday actions,
  the rest behind *More*; the status line replaces the four cards.
- Sync preview: files bound for the default folder are marked `!` and listed
  first, the "Why" column says what placed a file (your rule, folder name, file
  name), and files not downloaded are counted with their reason. After a sync the
  log lists what landed in the default folder and what was not downloaded, why.
- All other screens follow the same pattern: numbers and key words in bold, long
  course, file and folder names cut with "…", shorter hints. Settings shows the
  explanation only under the focused row; Courses drops the type column and
  marks new courses; What's new says "today 14:02"/"yesterday"; Doctor lists
  problems first under a one-line summary; Reorganize hides the empty table;
  the setup texts are shorter.
- README: installing with plain `pip` into a venv is documented next to uv and pipx.
- The release workflow can be started by hand as a dry run (checks, build, artifact
  hand-over, no upload), to test it after Dependabot updates its actions.

## [0.2.4] - 2026-10-15

### Changed
- With `unzip` on, the archive is removed after extracting it and not downloaded
  again: the extracted folder stands in for it. Only a changed archive is fetched
  again, as a new version next to the old folder ("Blatt_v2/"), so notes in the
  extracted folder are never overwritten. Archives extracted by older versions can
  simply be deleted by hand. Deleting the folder as well restores and re-extracts it.

## [0.2.3] - 2026-10-15

### Fixed
- A file only re-described or moved on Stud.IP (same content) kept its folder but
  was recorded with the new rule, so `studip reorganize` never offered to move it;
  the rule that placed it is kept now.
- `--include-large` also dropped `sync.exclude.folders` (e.g. `CoursewareImport*`).
- A rejected login during planning was caught per course and could clear the
  auto-sync pause; it now stops the sync. The terminal UI pauses auto-sync and asks
  for the password instead of only showing the error.
- The terminal UI wrote no log file; repeated logging setup no longer adds handlers.
- A file deleted in one Stud.IP folder and uploaded under the same name in another
  is no longer treated as a new version of it.
- Setup: course numbers are real roman numerals and only the trailing one counts
  ("X-Ray Physics I" finds "X-Ray Physics", "Seminar VII" finds "Seminar 7").
- Setup (UI and `studip init`) refuses `/`, the home folder or system folders as the
  course folder location before creating anything, and checks the config first.
- A transfer cut short at the same point twice was saved as complete; only a size
  within 10 % / 1 MB of the announced one, with identical content twice, is
  accepted as "Stud.IP reports a wrong size".
- A file that turned out unchanged now clears its failure count.
- Name rules suggested for the course root match umlauts (`Lösung_1.pdf`).
- Stud.IP answers with `[]` where an object is expected no longer crash.
- `$VISUAL`/`$EDITOR` with quoted arguments works; a missing editor is a message.
- Moves (renames on Stud.IP, reorganize, undo) are persisted with a directory fsync.

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

[Unreleased]: https://github.com/nbomers/studip-router/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/nbomers/studip-router/compare/v0.4.2...v0.5.0
[0.4.2]: https://github.com/nbomers/studip-router/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/nbomers/studip-router/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/nbomers/studip-router/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/nbomers/studip-router/compare/v0.2.4...v0.3.0
[0.2.4]: https://github.com/nbomers/studip-router/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/nbomers/studip-router/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/nbomers/studip-router/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/nbomers/studip-router/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/nbomers/studip-router/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/nbomers/studip-router/releases/tag/v0.1.0
