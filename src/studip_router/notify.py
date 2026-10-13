"""Desktop notifications (macOS Notification Center, notify-send on Linux)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys

log = logging.getLogger(__name__)


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def notify(title: str, message: str) -> bool:
    """Best effort; returns False if no notification mechanism is available."""
    try:
        if sys.platform == "darwin":
            script = (
                f"display notification {_applescript_string(message)} "
                f"with title {_applescript_string(title)}"
            )
            subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=10)
            return True
        if shutil.which("notify-send"):
            subprocess.run(
                ["notify-send", title, message], check=True, capture_output=True, timeout=10
            )
            return True
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("notification failed: %s", exc)
    return False


def sync_message(new_by_course: dict[str, int], news: int) -> str | None:
    parts = [f"{n} new in {course}" for course, n in sorted(new_by_course.items()) if n]
    if news:
        parts.append(f"{news} new announcement{'s' if news != 1 else ''}")
    return ", ".join(parts) if parts else None
