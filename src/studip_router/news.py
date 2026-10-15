"""Course announcements ("Ankündigungen")."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser

from .api import StudipClient
from .state import State


@dataclass(frozen=True)
class News:
    id: str
    course_id: str
    course: str
    title: str
    text: str
    published: datetime | None


class _TextExtractor(HTMLParser):
    BLOCK = frozenset({"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "ul", "ol", "table"})

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("• ")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(content: str) -> str:
    """Readable plain text from Stud.IP's HTML (or wiki markup) news body."""
    if "<" not in content:
        return html.unescape(content).strip()
    parser = _TextExtractor()
    parser.feed(content)
    text = "".join(parser.parts)  # HTMLParser already decoded entities
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _parse_dt(value: str | None) -> datetime | None:
    """Aware datetime; a time without offset is taken as local time."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except ValueError:
        return None
    if parsed is not None and parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def fetch_news(client: StudipClient, course_id: str, course_title: str) -> list[News]:
    result = []
    for item, _ in client._paginate(f"/courses/{course_id}/news"):
        attrs = item.get("attributes") or {}
        result.append(
            News(
                id=item["id"],
                course_id=course_id,
                course=course_title,
                title=attrs.get("title") or "(untitled)",
                text=html_to_text(attrs.get("content") or ""),
                published=_parse_dt(attrs.get("publication-start") or attrs.get("mkdate")),
            )
        )
    result.sort(key=lambda n: n.published or datetime.min.astimezone(), reverse=True)
    return result


def unread(state: State, items: list[News]) -> list[News]:
    seen = state.news_seen([n.id for n in items])
    return [n for n in items if n.id not in seen]
