# Troubleshooting and removal

[Back to the quick start](../README.md)

## Troubleshooting

| Problem | Fix |
|---|---|
| `authentication failed (HTTP 401)` | `studip auth login` (or `L` in settings); your account needs a Stud.IP password, not SSO-only |
| `JSON:API not available (HTTP 404)` | the JSON:API is disabled at your university; ask the Stud.IP admins |
| `target folder does not exist` | a rule points to a folder that is missing: create it, fix the rule target, or turn on *create folders* in the settings |
| `course folder does not exist` | the course folder was renamed or moved: point `target` to it (`c` in the UI) or create it |
| `received an HTML page instead of the file` | log in to Stud.IP in the browser and check the file is accessible |
| `another studip-router run is in progress` | auto-sync is running right now; try again in a minute |
| `auto-sync paused after a failed login` | `studip auth login` (or `L` in the settings) resumes it |
| auto-sync "waits after the update" | the new version would sort synced files differently: open `studip` (or run `studip sync` once) to look; `o` moves them |
| no notifications | `studip doctor --test-notification`; allow notifications for "Script Editor" in System Settings |
| background sync cannot read the password | `studip schedule run-now` and choose *Always Allow* when macOS asks for keychain access (again after `uv tool upgrade`) |
| a course is listed under "could not be read" | usually a temporary Stud.IP problem; the other courses were synced, the log has details |
| config errors | `studip config validate` names the exact key, e.g. `courses.<id>.rules[2].match.name_regex` |
| `keychain not accessible` | no OS keychain (server, WSL, container): install and unlock a Secret Service keyring, or set `STUDIP_ROUTER_PASSWORD` for headless use (never put it in a file others can read) |
| `Stud.IP sent text/html instead of JSON` | your university logs in via SSO, shows a maintenance page, or has the JSON:API disabled |
| `state … is damaged` | rename the state file as the message says and sync again; files on disk are recognised, nothing is downloaded twice |
| anything else | `studip doctor`, then the log at `~/.local/state/studip-router/studip-router.log` |

---


## Uninstall

```bash
studip schedule uninstall          # background job (per profile: --profile NAME)
studip auth logout                 # Stud.IP password from the keychain
studip pdf-password list           # then `studip pdf-password remove COURSE` for each
uv tool uninstall studip-router    # or: pipx uninstall studip-router
rm -r ~/.config/studip-router ~/.local/state/studip-router
```

Your course folders and downloaded files are never touched.


## Config updates, background paths and recovery

Relative `sync.root` values are resolved relative to the config file, so manual
and scheduled runs use the same folder. Surrounding whitespace is stripped.
The scheduler stores the resolved config and state paths at installation. After
moving them, reinstall the schedule. Password environment variables are never
copied into job files; headless jobs need their own secure runtime environment
or a usable OS keychain.

Config changes use a private lock, unique temporary files, file flush and atomic
replacement. A competing tool instance fails with a retry message; if an editor
changed the file before publication, reload it and try again. `config.toml.bak`
retains the prior text and is private. External editors do not honor the lock:
avoid saving in an editor at the exact instant another process changes config.

Doctor reports long path components, total path budget and free disk space.
Shorten `sync.root`, the course folder or the rule target when a path exceeds the
filesystem limit. The tool does not silently rename user folders. Temporary and
version suffixes are included in the path budget.

On disk-full/quota/read-only failures, free space or restore permissions and run
sync again. Completed files remain; incomplete temporary downloads are removed.
A completed file whose database write was interrupted is checked for adoption by
the next sync. Keep the database backup before attempting manual repairs.

Reorganize journals completed moves. If a run stops partway through, inspect its
preview and use `studip undo`; an occupied original location is kept. Re-running
reorganize plans remaining files. A move intent is journaled before publication. If a crash happened before the
completion record, Undo verifies the target against the tracked digest before
restoring it; it also recognizes two paths to the same partially moved hardlink.
If the content was edited or no digest is available, inspect both paths and
restore from your file backup. Semester changes retain old
course files; a failure may leave newly created empty folders to remove by hand.

Atomic no-overwrite moves require hard-link support on the destination
filesystem. On a filesystem without it, the operation fails safely and keeps the
source. Move the course to a supported local filesystem instead of disabling
collision protection. Network/cloud filesystems can also have weaker durability
or delayed visibility; keep independent backups of your annotations.
