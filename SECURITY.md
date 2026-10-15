# Security

`studip-router` handles your university password, so please report security
problems privately via GitHub's "Report a vulnerability" (Security tab) instead
of opening a public issue. If that is not available, open an issue that only asks
for a private contact, without details.

## Supported versions

Only the latest release (and `main`) gets fixes. Update with
`uv tool upgrade studip-router` or `pipx upgrade studip-router`.

## What to expect

- Acknowledgement within 7 days, an assessment within 14 days.
- A fix for a confirmed problem in the next release, with a note in the changelog
  that credits you (unless you prefer not to be named).
- This is a spare-time project without a bug bounty.

## What the tool guarantees

- The password is stored only in the OS keychain (or read from
  `STUDIP_ROUTER_PASSWORD` for headless use) and never written to the config,
  the state database, logs or any output. Crash reports of the terminal UI are
  printed without variable values. Without a usable keychain the tool stops with
  a message; it never falls back to storing the password in a file.
- Course PDF passwords (`studip pdf-password`) are kept in the OS keychain only, like
  the Stud.IP password.
- Credentials are only sent over HTTPS (plain HTTP only to `localhost`) and only to the
  configured Stud.IP host; redirects to other hosts do not receive them, and
  redirects to plain HTTP are refused. TLS
  certificates are always checked (with the operating system's certificate store).
- A rejected login is not repeated: one attempt per run, and background sync pauses
  until `studip auth login`.
- Archives are extracted with path checks (no ZIP slip, no absolute paths or drive
  letters), file names made safe, and size, entry-count and compression-ratio limits.
- The state database and log are readable only by the user.
- Text from Stud.IP (titles, file names, announcements) is never interpreted as
  terminal markup or links; downloads are stopped when far bigger than announced or
  than the size limit; endless or repeating pagination is stopped.
- The only host contacted is the configured Stud.IP (no telemetry, no update checks).

## In scope

Leaks of the password or session, writes outside the configured folders, deletion or
silent overwriting of user files, and code execution through files or API answers
from Stud.IP.

When filing bug reports, remove user names, course names and paths from logs.
