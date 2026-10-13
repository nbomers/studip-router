# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - unreleased

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
