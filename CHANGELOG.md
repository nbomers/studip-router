# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

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

[0.1.0]: https://github.com/nbomers/studip-router/releases/tag/v0.1.0
