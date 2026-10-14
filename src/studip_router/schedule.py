"""Opt-in background sync: a launchd agent on macOS, a systemd user timer on Linux.

Nothing here runs unless the user calls `studip schedule install`.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import paths

LABEL = "io.github.studip-router.sync"


@dataclass(frozen=True)
class ScheduleStatus:
    installed: bool
    path: Path
    interval_minutes: int | None
    loaded: bool
    detail: str = ""


def executable() -> str:
    """Absolute path of the `studip` command to run from the scheduler."""
    found = shutil.which("studip")
    if found:
        return str(Path(found).resolve())
    return str(Path(sys.argv[0]).resolve())


def executable_warning(exe: str) -> str | None:
    if "Mobile Documents" in exe or "/.venv" in exe or ".venv.nosync" in exe:
        return (
            "the scheduler would run studip from a project checkout; install it with "
            "`uv tool install .` (or pipx) first so the job keeps working"
        )
    return None


def label(profile: str | None = None) -> str:
    return LABEL + (f".{profile}" if profile else "")


def _unit(profile: str | None) -> str:
    return "studip-router" + (f"-{profile}" if profile else "")


def _plist_path(profile: str | None = None) -> Path:
    return Path("~/Library/LaunchAgents").expanduser() / f"{label(profile)}.plist"


def _systemd_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "systemd" / "user"


def launchd_plist(exe: str, minutes: int, profile: str | None) -> dict:
    args = [exe]
    if profile:
        args += ["--profile", profile]
    args += ["sync", "--notify", "--quiet"]
    log = str(paths.state_dir() / "scheduler.log")
    return {
        "Label": label(profile),
        "ProgramArguments": args,
        "StartInterval": minutes * 60,
        "RunAtLoad": False,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"},
    }


def _systemd_quote(arg: str) -> str:
    """One ExecStart argument, safe with spaces, quotes, ``%`` specifiers and ``$``."""
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped.replace("%", "%%").replace("$", "$$") + '"'


def systemd_units(exe: str, minutes: int, profile: str | None) -> tuple[str, str]:
    args = [exe, *(["--profile", profile] if profile else []), "sync", "--notify", "--quiet"]
    service = (
        "[Unit]\nDescription=studip-router sync\n\n"
        f"[Service]\nType=oneshot\nExecStart={' '.join(map(_systemd_quote, args))}\n"
    )
    timer = (
        "[Unit]\nDescription=Run studip-router sync periodically\n\n"
        f"[Timer]\nOnBootSec=5min\nOnUnitActiveSec={minutes}min\nPersistent=true\n\n"
        "[Install]\nWantedBy=timers.target\n"
    )
    return service, timer


def install(minutes: int, profile: str | None = None) -> Path:
    exe = executable()
    paths.state_dir().mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        plist = _plist_path(profile)
        plist.parent.mkdir(parents=True, exist_ok=True)
        if plist.exists():
            _launchctl("bootout", f"gui/{os.getuid()}/{label(profile)}", check=False)
        with plist.open("wb") as fh:
            plistlib.dump(launchd_plist(exe, minutes, profile), fh)
        _launchctl("bootstrap", f"gui/{os.getuid()}", str(plist))
        return plist
    if sys.platform.startswith("linux"):
        unit_dir = _systemd_dir()
        unit_dir.mkdir(parents=True, exist_ok=True)
        service, timer = systemd_units(exe, minutes, profile)
        unit = _unit(profile)
        (unit_dir / f"{unit}.service").write_text(service)
        (unit_dir / f"{unit}.timer").write_text(timer)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", f"{unit}.timer"], check=True)
        return unit_dir / f"{unit}.timer"
    raise OSError("scheduling is only supported on macOS (launchd) and Linux (systemd)")


def uninstall(profile: str | None = None) -> bool:
    """Stop and remove the job this tool installed. Returns False if none existed."""
    if sys.platform == "darwin":
        plist = _plist_path(profile)
        if not plist.exists():
            return False
        _launchctl("bootout", f"gui/{os.getuid()}/{label(profile)}", check=False)
        plist.unlink()
        return True
    unit_dir = _systemd_dir()
    unit = _unit(profile)
    timer = unit_dir / f"{unit}.timer"
    if not timer.exists():
        return False
    subprocess.run(["systemctl", "--user", "disable", "--now", f"{unit}.timer"], check=False)
    timer.unlink()
    (unit_dir / f"{unit}.service").unlink(missing_ok=True)
    return True


def status(profile: str | None = None) -> ScheduleStatus:
    if sys.platform == "darwin":
        plist = _plist_path(profile)
        if not plist.exists():
            return ScheduleStatus(False, plist, None, False)
        with plist.open("rb") as fh:
            data = plistlib.load(fh)
        result = _launchctl("print", f"gui/{os.getuid()}/{label(profile)}", check=False)
        return ScheduleStatus(
            True, plist, int(data.get("StartInterval", 0)) // 60, result.returncode == 0
        )
    timer = _systemd_dir() / f"{_unit(profile)}.timer"
    return ScheduleStatus(timer.exists(), timer, None, timer.exists())


def _launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], check=check, capture_output=True, text=True)


def run_now(profile: str | None = None) -> bool:
    """Kick off the installed job immediately. False if none is installed."""
    if sys.platform == "darwin":
        if not _plist_path(profile).exists():
            return False
        _launchctl("kickstart", f"gui/{os.getuid()}/{label(profile)}")
        return True
    timer = _systemd_dir() / f"{_unit(profile)}.timer"
    if not timer.exists():
        return False
    subprocess.run(["systemctl", "--user", "start", f"{_unit(profile)}.service"], check=True)
    return True
