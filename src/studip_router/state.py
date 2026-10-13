"""Persistent sync state in SQLite (kept outside any cloud-synced folder)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

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
    synced_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS files_course ON files(course_id);
CREATE TABLE IF NOT EXISTS runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    mode        TEXT NOT NULL,
    summary     TEXT
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
    local_path: str  # absolute, NFC
    local_size: int
    local_mtime: float
    sha256: str | None
    version: int = 1
    rule_id: str | None = None
    routed_by_default: bool = False
    synced_at: str = ""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class State:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

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

    def last_run(self) -> sqlite3.Row | None:
        return self._db.execute(
            "SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY run_id DESC LIMIT 1"
        ).fetchone()


def _record(row: sqlite3.Row) -> FileRecord:
    data = dict(row)
    data["routed_by_default"] = bool(data["routed_by_default"])
    return FileRecord(**data)
