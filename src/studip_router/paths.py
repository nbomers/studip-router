"""Locations of config, state and log files.

A *profile* selects a separate config and state (e.g. a second university).
The default profile uses ``config.toml`` / ``state.sqlite``; profile ``x`` uses
``profiles/x.toml`` / ``state-x.sqlite``. Environment variables override
everything (used by tests and the scheduler).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import platformdirs

APP_NAME = "studip-router"
_PROFILE_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_profile: str | None = None


def set_profile(name: str | None) -> None:
    global _profile
    if name is not None and not _PROFILE_RE.match(name):
        raise ValueError("profile names may contain letters, digits, '-' and '_' only")
    _profile = name or None


def profile() -> str | None:
    return _profile or os.environ.get("STUDIP_ROUTER_PROFILE") or None


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (
        "~/.config" if os.name != "nt" else platformdirs.user_config_dir()
    )
    # ~/.config on macOS too: Application Support is awkward to edit from a shell.
    return Path(base).expanduser() / APP_NAME


def config_file() -> Path:
    if env := os.environ.get("STUDIP_ROUTER_CONFIG"):
        return Path(env).expanduser()
    name = profile()
    return config_dir() / ("config.toml" if not name else f"profiles/{name}.toml")


def list_profiles() -> list[str]:
    folder = config_dir() / "profiles"
    return sorted(p.stem for p in folder.glob("*.toml")) if folder.is_dir() else []


def state_dir() -> Path:
    if env := os.environ.get("STUDIP_ROUTER_STATE_DIR"):
        return Path(env).expanduser()
    base = os.environ.get("XDG_STATE_HOME") or (
        "~/.local/state" if os.name != "nt" else platformdirs.user_state_dir()
    )
    return Path(base).expanduser() / APP_NAME


def state_db() -> Path:
    name = profile()
    return state_dir() / ("state.sqlite" if not name else f"state-{name}.sqlite")


def log_file() -> Path:
    return state_dir() / "studip-router.log"


def lock_file() -> Path:
    return state_dir() / f"{profile() or 'default'}.lock"


def auth_pause_file() -> Path:
    """Exists while auto-sync is paused after a failed login."""
    return state_dir() / f"{profile() or 'default'}.auth-paused"
