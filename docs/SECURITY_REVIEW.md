# Security and reliability follow-up

Date: 6 November 2026. Scope: the 45-item local audit and its implementation.
This is a source review with targeted offline reproductions and regression tests,
not a completed Codex Security scan. The skill's required preflight reference and
scan tools were unavailable. Findings are not claims of complete vulnerability
coverage. Real university accounts and production servers were not used.

## Findings and remediation

| Boundary | Original trigger | Change and evidence |
|---|---|---|
| No overwrite when moving | `os.link` fails; a user destination appears before fallback `os.rename`. | Replace the unsafe fallback with native exclusive rename; retain source if unsupported. Follow-up tests also preserve dangling destination symlinks. Original reproduction now raises without replacing either file. Existing successful move, sync, version and undo tests remain. |
| PDF merge ownership | Output created/edited during serialization, or after the initial ownership check during input inspection. | Capture existence and authorized digest once; new outputs use no-clobber publication, existing outputs recheck against that same digest. Four race regressions preserve user bytes. Normal first/update merge tests remain. |
| PDF unlocking | Local encrypted file edited during PDF generation. | Compare against the original input digest before publication. Normal encrypted, unencrypted, wrong-password and malformed-PDF tests remain. |
| API response resources | Oversized/deep JSON or an interrupted streamed body. | Limit decompressed JSON to 16 MiB, close responses on all outcomes, retry interrupted reads, convert exhausted failures to `ApiError`. Tests cover early stop, eventual success and exhausted retries. |
| API credential destination | A direct API URL outside the configured origin. | Reject it before HTTP dispatch. HTTPS CDN redirects for downloads remain compatible and receive no Stud.IP credentials, as covered by existing redirect tests. Reject credential-bearing/malformed server URLs. |
| Filesystem destinations | Rule/default folder is a symlink outside the course or total path exceeds filesystem limits. | Plan reports an error and executor checks containment again. Doctor explains folder/total length budgets. This is not a defense against same-account concurrent parent-directory replacement. |
| File handlers | Unknown downloaded extension passed directly to the OS opener. | Keep program/shortcut extension blocking; ask before opening unknown types; show opener failures as UI errors. External document viewers remain a trust boundary. |
| Credential integrity | Setup stripped leading/trailing password spaces. | Preserve password input verbatim; normalize only URL and username. Passwords remain in OS keychain/runtime variables, not config or state. |
| Local config | Concurrent writers share `.tmp` and overwrite newer changes; temp/backup privacy inconsistent. | Per-config lock, unique mode-0600 temporary/backup files, fsync and replacement; detect external edits. Tests inject lock contention, stale text and storage failures. |

A separate read-only investigator reproduced the move and merge loss paths before
patching. A fresh independent reviewer found the intermediate merge-baseline and
JSON-body-retry gaps. Both were corrected and regression tests added. No remote
attacker's ability to modify local files was established; the concurrency findings
are local data-integrity issues within ordinary editor/cloud-sync workflows.

## Existing protections checked

The suite exercises login pause after HTTP 401, no secret leakage in tested errors,
keyring failures, TLS downgrade refusal, cross-origin redirect credential removal,
ZIP traversal/entry/size/ratio limits, terminal markup/control text, changed local
files, file collisions, interrupted transfers, repeat/endless pagination, state
migration and update opt-in behavior. Release actions remain SHA-pinned with read
permissions by default; only the PyPI publish job has OIDC permission.

Storage fault injection covers ENOSPC, EDQUOT and EROFS config writes, failed
file flush, database failure after a completed download, and interrupted
reorganization followed by undo of completed moves. New runs adopt complete
unrecorded downloads rather than downloading again. These tests simulate failures;
they do not establish the durability guarantees of every cloud/network filesystem.
Reorganization writes a move intent before publication. Undo verifies a digest
before reverting a move interrupted before its completion record; an interrupted
hardlink/unlink is recognized by inode identity. Ambiguous or edited targets still
require manual inspection, described in Troubleshooting. Old semester files are retained; partial folder creation may
leave empty new folders on a failed semester setup.

## Dependency audit

`pip-audit 2.10.1` queried PyPI advisories against the locked runtime dependency
export. The original lock had known advisories for pypdf 6.20.1 and certifi
2026.7.22. The updated lock uses pypdf 6.21.4 and certifi 2026.10.23; project lower
bounds now exclude the vulnerable versions. The repeat audit returned **zero known
advisories across 31 exported dependency entries**. Platform marker entries can
repeat packages; this is not 31 distinct direct dependencies. The machine-readable
result is [dependency-audit.json](dependency-audit.json). CI repeats the hashed
runtime export audit; future advisories can change this result.

## Remaining validation limits

- The managed test sandbox hangs during a bare Python threadpool shutdown. The
  same minimal program and the test suite terminate with the execution tool's
  network permission enabled; tests still block real socket connections.
- Tests ran on Linux/Python 3.12. macOS/APFS, real iCloud/OneDrive/network drives,
  actual OS keychains, launchd/systemd deployment and hardware power-loss were not
  exercised here. Existing CI includes Linux/macOS and Python 3.11–3.14; it was not
  run remotely during this task.
- Headless keyboard and geometry tests cover small terminals and resize; no real
  screenreader/VoiceOver session was available. Terminal compatibility requires
  testing with the actual emulator and assistive technology.
- Local non-cooperating editors retain the final hash-check/replacement race;
  same-account directory substitution is not fully prevented. Document viewers,
  user regexes and PDF parsers are not isolated in a resource-limited sandbox.

Final local validation: **678 tests passed in 42.90 s**, Ruff lint/format, mypy
(50 source files), whitespace checks and wheel/sdist build passed. The tests use
fake/offline Stud.IP data and a null keyring. Isolated wheel smoke tests
(`studip --version` and `studip rules --help`) also passed.

See [the numbered status list](VERBESSERUNGSLISTE.md) for each item's result.

PR integration: current main (`51452a5`) was merged before review. Its scoped
PDF-password storage, OS-keyring restrictions, safe-by-default publication, ZIP
limits/retries and undo checkpoints are preserved in the split workflow modules.
The audit regression tests use the existing merge-result API for refused writes.
