"""A report about a file sorted into the wrong folder (roadmap item 18).

The text holds only what the keyword lists need: the Stud.IP folder name, the
file name, what placed it and where. No course name, no IDs, no paths outside
the course folder. It is shown to you first; you edit it (folder names can
contain people's names) and send it yourself: nothing is ever sent automatically.
"""

from __future__ import annotations

from urllib.parse import urlencode

from . import __version__
from .planner import Route

ISSUES = "https://github.com/nbomers/studip-router/issues/new"


def placed_by(route: Route | None) -> str:
    if route is None:
        return "unknown"
    if route.by_default:
        return "nothing matched (default folder)"
    if route.rule_id and route.rule_id.startswith("auto:"):
        return f"automatic sorting, category {route.rule_id[5:]}" + (
            f" (by {route.why})" if route.why else ""
        )
    return f"your own rule {route.rule_id}" if route.rule_id else "unknown"


def report_text(folder: str, name: str, route: Route | None) -> tuple[str, str]:
    """(title, body) for an issue about one file."""
    target = (route.subdir or ".") if route else "?"
    title = f"Sorted wrong: {name}"
    body = "\n".join(
        [
            f"studip-router {__version__}",
            "",
            f"Stud.IP folder: {folder or '(course root)'}",
            f"File name: {name}",
            f"Placed by: {placed_by(route)}",
            f"Went to: {target}",
            "Should go to: ",
            "",
            "(Check before sending: remove names of people from folder and file names.)",
        ]
    )
    return title, body


def issue_url(title: str, body: str) -> str:
    return f"{ISSUES}?{urlencode({'title': title, 'body': body})}"
