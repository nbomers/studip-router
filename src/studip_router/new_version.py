"""Optional notice about a newer release (off by default: ``[ui] update_check``).

The only request outside Stud.IP that studip-router can make, and only when you
turn it on: once a week a GET of ``pypi.org/pypi/studip-router/json``. It sends
nothing but the request itself (your IP address and the User-Agent
``studip-router/<version>``). The answer is cached; a failed request is quiet.
The background sync never makes it.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from collections.abc import Callable

from . import __version__, paths

URL = "https://pypi.org/pypi/studip-router/json"
INTERVAL = 7 * 86400  # seconds between two requests


def _parse(version: str) -> tuple[int, ...] | None:
    """Final releases only: "0.5.1" -> (0, 5, 1); "0.6.0rc1" -> None."""
    if not re.fullmatch(r"\d+(?:\.\d+)*", version.strip()):
        return None
    return tuple(int(p) for p in version.strip().split("."))


def is_newer(latest: str, current: str = __version__) -> bool:
    new, old = _parse(latest), _parse(current)
    return new is not None and old is not None and new > old


def _fetch() -> str | None:
    import httpx

    from .api import ssl_context

    response = httpx.get(
        URL,
        timeout=5,
        verify=ssl_context(),
        headers={"User-Agent": f"studip-router/{__version__}", "Accept": "application/json"},
        follow_redirects=False,
    )
    response.raise_for_status()
    version = response.json()["info"]["version"]
    return version if isinstance(version, str) else None


def _load() -> dict:
    try:
        data = json.loads(paths.version_check_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    path = paths.version_check_file()
    with contextlib.suppress(OSError):
        paths.private_dir(path.parent)
        path.write_text(json.dumps(data), encoding="utf-8")


def cached_newer() -> str | None:
    """The newer version found by the last check, without asking PyPI."""
    latest = _load().get("latest")
    return latest if isinstance(latest, str) and is_newer(latest) else None


def check(*, fetch: Callable[[], str | None] | None = None, now: float | None = None) -> str | None:
    """A newer version than this one, asking PyPI at most once per ``INTERVAL``."""
    now = time.time() if now is None else now
    data = _load()
    checked = data.get("checked")
    if not isinstance(checked, (int, float)) or now - checked >= INTERVAL:
        latest = None
        with contextlib.suppress(Exception):  # offline, PyPI down, odd answer: quiet
            latest = (fetch or _fetch)()
        data = {"checked": now, "latest": latest or data.get("latest")}
        _save(data)
    return cached_newer()


def hint(version: str) -> str:
    return f"studip-router {version} is available - `uv tool upgrade studip-router`"
