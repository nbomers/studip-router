"""Plain data types shared across modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Semester:
    id: str
    title: str
    start: datetime
    end: datetime

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment <= self.end


@dataclass(frozen=True)
class Course:
    id: str
    title: str
    start_semester: Semester | None  # None: no semester (study groups, "unlimited")
    end_semester: Semester | None  # None means "unlimited"
    course_type: str = ""

    def runs_in(self, semester: Semester) -> bool:
        """True if the course is active during ``semester``."""
        if self.start_semester is None:
            return True  # study groups and courses without semester are always current
        if self.start_semester.start > semester.start:
            return False
        return self.end_semester is None or self.end_semester.end >= semester.end

    @property
    def semester_title(self) -> str:
        if self.start_semester is None:
            return "no semester"
        if self.end_semester is None or self.end_semester.id == self.start_semester.id:
            return self.start_semester.title
        return f"{self.start_semester.title} - {self.end_semester.title}"


@dataclass(frozen=True)
class Folder:
    id: str
    name: str
    parent_id: str | None
    folder_type: str


@dataclass(frozen=True)
class RemoteFile:
    id: str
    course_id: str
    name: str
    folder_path: str  # "/"-joined Stud.IP folder names below the course root
    size: int
    chdate: datetime
    mime_type: str
    description: str
    downloadable: bool
    content_id: str = ""  # id of the stored file; changes only when the content is replaced
    folder_type: str = ""  # Stud.IP type of the parent folder, e.g. "HomeworkFolder"
