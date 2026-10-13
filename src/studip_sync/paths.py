"""Locations of config, state and log files (overridable for tests)."""

from __future__ import annotations

import os
from pathlib import Path

import platformdirs

APP_NAME = "studip-sync"


def config_file() -> Path:
    if env := os.environ.get("STUDIP_SYNC_CONFIG"):
        return Path(env).expanduser()
    # ~/.config on macOS too: platformdirs' macOS default (Application Support)
    # is unusual for CLI tools and awkward to edit from a shell.
    base = os.environ.get("XDG_CONFIG_HOME") or (
        "~/.config" if os.name != "nt" else platformdirs.user_config_dir()
    )
    return Path(base).expanduser() / APP_NAME / "config.toml"


def state_dir() -> Path:
    if env := os.environ.get("STUDIP_SYNC_STATE_DIR"):
        return Path(env).expanduser()
    base = os.environ.get("XDG_STATE_HOME") or (
        "~/.local/state" if os.name != "nt" else platformdirs.user_state_dir()
    )
    return Path(base).expanduser() / APP_NAME


def state_db() -> Path:
    return state_dir() / "state.sqlite"


def log_file() -> Path:
    return state_dir() / "studip-sync.log"
