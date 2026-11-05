"""Course dates as an iCalendar file (RFC 5545) for Apple, Google or Thunderbird."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from .models import CourseDate

PRODID = "-//studip-router//course dates//EN"


def _text(value: str) -> str:
    """TEXT value: backslash, ';', ',' and newlines escaped; no control characters."""
    value = "".join(c for c in value if c >= " " or c == "\n")
    return value.replace("\\", "\\\\").replace(";", r"\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Lines longer than 75 octets are folded (continuation lines start with a space)."""
    data = line.encode()
    if len(data) <= 75:
        return line
    parts: list[str] = []
    while data:
        cut = 75 if not parts else 74
        while cut < len(data) and (data[cut] & 0xC0) == 0x80:  # never split a UTF-8 character
            cut -= 1
        parts.append(data[:cut].decode())
        data = data[cut:]
    return "\r\n ".join(parts)


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def to_ics(dates: Iterable[CourseDate], now: datetime | None = None) -> str:
    stamp = _utc(now or datetime.now(UTC))
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN"]
    for d in sorted(dates, key=lambda d: d.start):
        summary = f"{d.course}: {d.category}" if d.category else d.course
        lines += [
            "BEGIN:VEVENT",
            f"UID:{_text(d.id)}@studip-router",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{_utc(d.start)}",
            f"DTEND:{_utc(d.end)}",
            f"SUMMARY:{_text(('Cancelled: ' if d.cancelled else '') + summary)}",
        ]
        if d.location:
            lines.append(f"LOCATION:{_text(d.location)}")
        if d.description:
            lines.append(f"DESCRIPTION:{_text(d.description)}")
        if d.cancelled:
            lines.append("STATUS:CANCELLED")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
