"""`studip doctor`: check everything that can go wrong before a sync does."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

import httpx

from . import __version__, auth, paths, schedule
from .api import ApiError, StudipClient, ssl_context
from .config import Config, ConfigError, load
from .fsutil import local_stat, walk_files


class Level(StrEnum):
    OK = "ok"
    INFO = "info"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class Check:
    name: str
    level: Level
    detail: str


def run_checks(*, offline: bool = False) -> Iterator[Check]:
    yield Check(
        "version", Level.INFO, f"studip-router {__version__}, Python {sys.version.split()[0]}"
    )
    try:
        cfg = load(paths.config_file())
    except ConfigError as exc:
        yield Check("config", Level.FAIL, str(exc))
        return
    active = [c for c in cfg.courses.values() if not c.exclude]
    yield Check("config", Level.OK, f"{cfg.path} ({len(active)} active courses)")
    yield from _check_paths(cfg)
    yield from _check_keychain()
    yield from _check_state()
    yield from _check_install()
    yield from _check_paused()
    yield from _check_schedule()
    if not offline:
        yield from _check_server(cfg)


def _check_paths(cfg: Config) -> Iterator[Check]:
    if not cfg.root.is_dir():
        yield Check("root folder", Level.FAIL, f"{cfg.root} does not exist")
        return
    writable = os.access(cfg.root, os.W_OK)
    yield Check(
        "root folder",
        Level.OK if writable else Level.FAIL,
        f"{cfg.root}" + ("" if writable else " is not writable"),
    )
    for course in cfg.courses.values():
        if course.exclude:
            continue
        directory = cfg.course_dir(course)
        label = course.name or course.target
        if cfg.root not in directory.parents and directory != cfg.root:
            yield Check(
                f"course {label}", Level.INFO, f"target {directory} is outside the root folder"
            )
        if not directory.is_dir():
            yield Check(f"course {label}", Level.FAIL, f"target {directory} does not exist")
            continue
        missing = [
            t
            for t in {
                cfg.course_default_target(course),
                *(r.target for r in course.rules if r.target),
            }
            if not (directory / t).is_dir()
        ]
        if missing and not cfg.create_missing_dirs:
            hint = (
                " (Finder shows ':' as '/'; write 'A : B' in the config)"
                if any(" / " in m for m in missing)
                else ""
            )
            yield Check(
                f"course {label}",
                Level.WARN,
                f"missing subfolders: {', '.join(sorted(missing))}{hint}",
            )
        else:
            yield Check(f"course {label}", Level.OK, str(directory))
    if "Mobile Documents" in str(cfg.root):
        evicted = sum(
            1
            for c in cfg.courses.values()
            if not c.exclude
            for f in walk_files(cfg.course_dir(c))
            if (st := local_stat(f)) and st.dataless
        )
        yield Check(
            "iCloud",
            Level.INFO,
            f"root is in iCloud Drive; {evicted} file(s) currently only in the cloud "
            "(they are never downloaded just for comparison)",
        )


def _check_keychain() -> Iterator[Check]:
    if os.environ.get("STUDIP_ROUTER_PASSWORD"):
        yield Check("keychain", Level.INFO, "password taken from STUDIP_ROUTER_PASSWORD")
        return
    problem = auth.keychain_problem()
    if problem:
        yield Check("keychain", Level.FAIL, problem)
    else:
        yield Check("keychain", Level.OK, "OS keychain available")


def _check_state() -> Iterator[Check]:
    db = paths.state_db()
    if "Mobile Documents" in str(db) or "Dropbox" in str(db):
        yield Check("state", Level.WARN, f"{db} is in a synced folder; SQLite may get corrupted")
    else:
        yield Check("state", Level.OK, str(db))


def _check_install() -> Iterator[Check]:
    """An editable install from a cloud folder breaks when the sources are evicted."""
    import json
    from importlib.metadata import PackageNotFoundError, distribution
    from urllib.parse import unquote

    try:
        info = json.loads(distribution("studip-router").read_text("direct_url.json") or "{}")
    except (PackageNotFoundError, ValueError):
        return
    if not (info.get("dir_info") or {}).get("editable"):
        return
    source = unquote(info.get("url", ""))
    if any(m in source for m in ("Mobile Documents", "Dropbox", "OneDrive", "Google Drive")):
        yield Check(
            "installation",
            Level.WARN,
            "installed in editable mode from a cloud-synced folder; if the sources "
            "are evicted, studip (and auto-sync) stops working. Use "
            "`uv tool install --reinstall .` without --editable for daily use",
        )


def _check_paused() -> Iterator[Check]:
    if paths.auth_pause_file().exists():
        yield Check(
            "auto-sync", Level.FAIL, "paused after a failed login - run `studip auth login`"
        )


def _check_schedule() -> Iterator[Check]:
    try:
        st = schedule.status(paths.profile())
    except OSError:
        return
    if not st.installed:
        yield Check("auto-sync", Level.INFO, "not installed (studip schedule install)")
        return
    level = Level.OK if st.loaded else Level.WARN
    every = f"every {st.interval_minutes} min" if st.interval_minutes else "installed"
    yield Check("auto-sync", level, f"{every}, {'loaded' if st.loaded else 'not loaded'}")
    if warning := schedule.executable_warning(schedule.executable()):
        yield Check("auto-sync", Level.WARN, warning)


def _check_server(cfg: Config) -> Iterator[Check]:
    try:
        r = httpx.get(
            cfg.base_url + "/jsonapi.php/v1/discovery",
            timeout=15,
            follow_redirects=True,
            verify=ssl_context(),  # same certificates as the sync itself
        )
    except httpx.HTTPError as exc:
        yield Check("server", Level.FAIL, f"{cfg.base_url} not reachable: {exc}")
        return
    if r.status_code != 200:
        yield Check(
            "server",
            Level.FAIL,
            f"JSON:API not available (HTTP {r.status_code}); ask your Stud.IP admins to enable it",
        )
        return
    yield Check("server", Level.OK, f"{cfg.base_url} JSON:API reachable")
    if paths.auth_pause_file().exists():
        # another login attempt with a password that already failed could lock the account
        yield Check("login", Level.WARN, "not tested while auto-sync is paused (studip auth login)")
        return
    if not auth.has_password(cfg.base_url, cfg.username):
        yield Check("login", Level.FAIL, "no password stored; run `studip auth login`")
        return
    try:
        client = StudipClient(
            cfg.base_url, cfg.username, auth.get_password(cfg.base_url, cfg.username)
        )
    except (ApiError, auth.AuthError) as exc:
        yield Check("login", Level.FAIL, str(exc))
        return
    with client:  # one connection for the login and the Courseware check
        try:
            client.me()
            sem = client.current_semester()
        except (ApiError, auth.AuthError) as exc:
            yield Check("login", Level.FAIL, str(exc))
            return
        except Exception as exc:  # unexpected answer: report, never crash the checks
            name = type(exc).__name__
            yield Check("login", Level.FAIL, f"unexpected answer from Stud.IP ({name})")
            return
        yield Check("login", Level.OK, f"as {cfg.username}; current semester: {sem.title}")
        try:
            courseware = [
                c.name or c.target
                for c in cfg.courses.values()
                if not c.exclude and client.courseware_units(c.id)
            ]
        except ApiError as exc:
            yield Check("courseware", Level.WARN, f"not checked: {exc}")
            return
    if courseware:
        yield Check(
            "courseware",
            Level.INFO,
            f"{len(courseware)} course(s) use Courseware ({', '.join(courseware)}); "
            + (
                "its files are synced"
                if cfg.courseware
                else "turn on 'courseware' in the settings to sync its files"
            ),
        )
