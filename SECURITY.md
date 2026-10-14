# Security

`studip-router` handles your university password, so please report security
problems privately via GitHub's "Report a vulnerability" (Security tab) instead
of opening a public issue.

What the tool guarantees:

- The password is stored only in the OS keychain (or read from
  `STUDIP_ROUTER_PASSWORD` for headless use) and never written to the config,
  the state database, logs or any output.
- Credentials are only sent over HTTPS (plain HTTP only to `localhost`) and only to the
  configured Stud.IP host; redirects to other
  hosts do not receive them.
- Archives are extracted with path checks (no ZIP slip) and size limits.

When filing bug reports, remove user names, course names and paths from logs.
