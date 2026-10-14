"""Persistent sync state in SQLite (kept outside any cloud-synced folder)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import CourseDate

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    file_id            TEXT PRIMARY KEY,
    course_id          TEXT NOT NULL,
    remote_name        TEXT NOT NULL,
    remote_folder_path TEXT NOT NULL,
    chdate             TEXT NOT NULL,
    filesize           INTEGER NOT NULL,
    local_path         TEXT NOT NULL,
    local_size         INTEGER NOT NULL,
    local_mtime        REAL NOT NULL,
    sha256             TEXT,
    version            INTEGER NOT NULL DEFAULT 1,
    rule_id            TEXT,
    routed_by_default  INTEGER NOT NULL DEFAULT 0,
    synced_at          TEXT NOT NULL,
    mime_type          TEXT NOT NULL DEFAULT '',
    description        TEXT NOT NULL DEFAULT '',
    content_id         TEXT NOT NULL DEFAULT '',
    gone               INTEGER NOT NULL DEFAULT 0,
    local_sha256       TEXT
);
CREATE TABLE IF NOT EXISTS ignored (
    file_id   TEXT PRIMARY KEY,
    course_id TEXT NOT NULL,
    name      TEXT NOT NULL,
    since     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS failures (
    file_id    TEXT PRIMARY KEY,
    count      INTEGER NOT NULL,
    last_error TEXT NOT NULL,
    last_try   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS files_course ON files(course_id);
CREATE TABLE IF NOT EXISTS runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    mode        TEXT NOT NULL,
    summary     TEXT
);
CREATE TABLE IF NOT EXISTS generated (
    path        TEXT PRIMARY KEY,
    sha256      TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS news_seen (
    news_id   TEXT PRIMARY KEY,
    course_id TEXT NOT NULL,
    seen_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS course_dates (
    date_id     TEXT PRIMARY KEY,
    course_id   TEXT NOT NULL,
    course      TEXT NOT NULL,
    start       TEXT NOT NULL,
    end         TEXT NOT NULL,
    category    TEXT NOT NULL,
    location    TEXT NOT NULL,
    cancelled   INTEGER NOT NULL,
    description TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS course_dates_course ON course_dates(course_id);
CREATE TABLE IF NOT EXISTS date_changes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id   TEXT NOT NULL,
    date_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    start       TEXT NOT NULL,
    text        TEXT NOT NULL,
    detected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS journal (
    run_id INTEGER NOT NULL,
    seq    INTEGER NOT NULL,
    op     TEXT NOT NULL,
    src    TEXT,
    dst    TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
"""


@dataclass
class FileRecord:
    file_id: str
    course_id: str
    remote_name: str
    remote_folder_path: str
    chdate: str  # ISO 8601 as delivered by the API
    filesize: int
    local_path: str  # absolute, as on disk (NFD on some file systems)
    local_size: int
    local_mtime: float
    sha256: str | None  # content as downloaded from Stud.IP
    version: int = 1
    rule_id: str | None = None
    routed_by_default: bool = False
    synced_at: str = ""
    mime_type: str = ""
    description: str = ""
    content_id: str = ""
    gone: bool = False  # deleted on Stud.IP and already reported
    local_sha256: str | None = None  # local content if it differs (decrypted PDF)

    @property
    def local_digest(self) -> str | None:
        """Hash of the file as we left it on disk."""
        return self.local_sha256 or self.sha256


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class State:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        columns = {row["name"] for row in self._db.execute("PRAGMA table_info(files)")}
        for name, kind in (
            ("mime_type", "TEXT NOT NULL DEFAULT ''"),
            ("description", "TEXT NOT NULL DEFAULT ''"),
            ("content_id", "TEXT NOT NULL DEFAULT ''"),
            ("gone", "INTEGER NOT NULL DEFAULT 0"),
            ("local_sha256", "TEXT"),
        ):
            if name not in columns:
                self._db.execute(f"ALTER TABLE files ADD COLUMN {name} {kind}")
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> State:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(self, file_id: str) -> FileRecord | None:
        row = self._db.execute("SELECT * FROM files WHERE file_id = ?", (file_id,)).fetchone()
        return _record(row) if row else None

    def for_course(self, course_id: str) -> dict[str, FileRecord]:
        rows = self._db.execute("SELECT * FROM files WHERE course_id = ?", (course_id,))
        return {row["file_id"]: _record(row) for row in rows}

    def all(self) -> list[FileRecord]:
        return [_record(r) for r in self._db.execute("SELECT * FROM files ORDER BY local_path")]

    def upsert(self, record: FileRecord) -> None:
        record.synced_at = record.synced_at or now_iso()
        data = asdict(record)
        data["routed_by_default"] = int(record.routed_by_default)
        data["gone"] = int(record.gone)
        cols = ", ".join(data)
        marks = ", ".join(f":{k}" for k in data)
        self._db.execute(f"INSERT OR REPLACE INTO files ({cols}) VALUES ({marks})", data)
        self._db.commit()

    def start_run(self, mode: str) -> int:
        cur = self._db.execute(
            "INSERT INTO runs (started_at, mode) VALUES (?, ?)", (now_iso(), mode)
        )
        self._db.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, summary: dict) -> None:
        self._db.execute(
            "UPDATE runs SET finished_at = ?, summary = ? WHERE run_id = ?",
            (now_iso(), json.dumps(summary, ensure_ascii=False), run_id),
        )
        self._db.commit()

    def journal(self, run_id: int, op: str, dst: str, src: str | None = None) -> None:
        seq = self._db.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM journal WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        self._db.execute(
            "INSERT INTO journal (run_id, seq, op, src, dst) VALUES (?, ?, ?, ?, ?)",
            (run_id, seq, op, src, dst),
        )
        self._db.commit()

    def runs(self, limit: int = 10) -> list[sqlite3.Row]:
        return list(self._db.execute("SELECT * FROM runs ORDER BY run_id DESC LIMIT ?", (limit,)))

    def journal_entries(self, run_id: int) -> list[sqlite3.Row]:
        return list(
            self._db.execute("SELECT * FROM journal WHERE run_id = ? ORDER BY seq", (run_id,))
        )

    def recent_files(self, limit: int = 50) -> list[FileRecord]:
        rows = self._db.execute(
            "SELECT * FROM files ORDER BY synced_at DESC, local_path LIMIT ?", (limit,)
        )
        return [_record(r) for r in rows]

    def default_routed(self) -> list[FileRecord]:
        rows = self._db.execute(
            "SELECT * FROM files WHERE routed_by_default = 1 ORDER BY course_id, local_path"
        )
        return [_record(r) for r in rows]

    def mark_gone(self, file_ids: list[str]) -> None:
        self._db.executemany(
            "UPDATE files SET gone = 1 WHERE file_id = ?", [(f,) for f in file_ids]
        )
        self._db.commit()

    def unmark_gone(self, file_ids: list[str]) -> None:
        self._db.executemany(
            "UPDATE files SET gone = 0 WHERE file_id = ?", [(f,) for f in file_ids]
        )
        self._db.commit()

    # -- ignored files ------------------------------------------------------------

    def ignored_ids(self) -> set[str]:
        return {r[0] for r in self._db.execute("SELECT file_id FROM ignored")}

    def ignored(self) -> list[sqlite3.Row]:
        return list(self._db.execute("SELECT * FROM ignored ORDER BY course_id, name"))

    def ignore(self, file_id: str, course_id: str, name: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO ignored VALUES (?, ?, ?, ?)",
            (file_id, course_id, name, now_iso()),
        )
        self._db.commit()

    def unignore(self, file_id: str) -> bool:
        cur = self._db.execute("DELETE FROM ignored WHERE file_id = ?", (file_id,))
        self._db.commit()
        return cur.rowcount > 0

    # -- failures (to back off from files that keep failing) ---------------------

    def failures(self) -> dict[str, tuple[int, str, str]]:
        rows = self._db.execute("SELECT file_id, count, last_error, last_try FROM failures")
        return {r[0]: (r[1], r[2], r[3]) for r in rows}

    def record_failure(self, file_id: str, error: str) -> None:
        self._db.execute(
            "INSERT INTO failures VALUES (?, 1, ?, ?) ON CONFLICT(file_id) DO UPDATE SET "
            "count = count + 1, last_error = excluded.last_error, last_try = excluded.last_try",
            (file_id, error[:500], now_iso()),
        )
        self._db.commit()

    def clear_failure(self, file_id: str) -> None:
        self._db.execute("DELETE FROM failures WHERE file_id = ?", (file_id,))
        self._db.commit()

    def delete_file_record(self, file_id: str) -> None:
        """Forget a file (used by undo); the local file itself is never touched here."""
        self._db.execute("DELETE FROM files WHERE file_id = ?", (file_id,))
        self._db.commit()

    def generated(self, path: str) -> tuple[str, str] | None:
        row = self._db.execute(
            "SELECT sha256, fingerprint FROM generated WHERE path = ?", (path,)
        ).fetchone()
        return (row["sha256"], row["fingerprint"]) if row else None

    def set_generated(self, path: str, sha256: str, fingerprint: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO generated VALUES (?, ?, ?, ?)",
            (path, sha256, fingerprint, now_iso()),
        )
        self._db.commit()

    def news_seen(self, news_ids: list[str]) -> set[str]:
        if not news_ids:
            return set()
        marks = ",".join("?" * len(news_ids))
        rows = self._db.execute(
            f"SELECT news_id FROM news_seen WHERE news_id IN ({marks})", news_ids
        )
        return {r[0] for r in rows}

    def mark_news_seen(self, items: list[tuple[str, str]]) -> None:
        self._db.executemany(
            "INSERT OR IGNORE INTO news_seen VALUES (?, ?, ?)",
            [(nid, cid, now_iso()) for nid, cid in items],
        )
        self._db.commit()

    def last_run(self) -> sqlite3.Row | None:
        return self._db.execute(
            "SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY run_id DESC LIMIT 1"
        ).fetchone()

    # -- course dates ----------------------------------------------------------------

    def course_dates(self, course_id: str) -> list[CourseDate]:
        rows = self._db.execute("SELECT * FROM course_dates WHERE course_id = ?", (course_id,))
        return [_course_date(r) for r in rows]

    def all_course_dates(self) -> list[CourseDate]:
        return [_course_date(r) for r in self._db.execute("SELECT * FROM course_dates")]

    def replace_course_dates(self, course_id: str, dates: list[CourseDate]) -> None:
        self._db.execute("DELETE FROM course_dates WHERE course_id = ?", (course_id,))
        self._db.executemany(
            "INSERT OR REPLACE INTO course_dates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    d.id,
                    d.course_id,
                    d.course,
                    d.start.isoformat(),
                    d.end.isoformat(),
                    d.category,
                    d.location,
                    int(d.cancelled),
                    d.description,
                )
                for d in dates
            ],
        )
        self._db.commit()

    def add_date_changes(self, changes: Iterable[tuple[str, str, str, datetime, str]]) -> None:
        """(course_id, date_id, kind, start, text) as found by a check."""
        self._db.executemany(
            "INSERT INTO date_changes (course_id, date_id, kind, start, text, detected_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [(c, d, k, s.isoformat(), t, now_iso()) for c, d, k, s, t in changes],
        )
        self._db.commit()

    def date_changes_since(self, since: datetime) -> list[sqlite3.Row]:
        return list(
            self._db.execute(
                "SELECT * FROM date_changes WHERE detected_at >= ? ORDER BY start",
                (since.astimezone(UTC).isoformat(timespec="seconds"),),
            )
        )


def _record(row: sqlite3.Row) -> FileRecord:
    data = dict(row)
    data["routed_by_default"] = bool(data["routed_by_default"])
    data["gone"] = bool(data.get("gone", 0))
    return FileRecord(**data)


def _course_date(row: sqlite3.Row) -> CourseDate:
    return CourseDate(
        id=row["date_id"],
        course_id=row["course_id"],
        course=row["course"],
        start=datetime.fromisoformat(row["start"]),
        end=datetime.fromisoformat(row["end"]),
        category=row["category"],
        location=row["location"],
        cancelled=bool(row["cancelled"]),
        description=row["description"],
    )
