"""Setup helpers (UI and `studip init`): find the course folders, match courses, write a config."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from .config import DEFAULT_COURSE_FOLDERS
from .fsutil import is_ignored, nfc, sanitize_filename
from .tomlwrite import key, value

# Words that carry no meaning for matching a course title to a folder name.
STOPWORDS = {
    "einführung",
    "einfuehrung",
    "in",
    "die",
    "der",
    "das",
    "den",
    "und",
    "für",
    "fuer",
    "of",
    "to",
    "the",
    "and",
    "for",
    "introduction",
    "v",
    "ü",
    "ue",
    "vl",
    "vorlesung",
    "informatiker",
    "innen",
    "informatikerinnen",
    "a",
    "i",
    "ii",
}


def _tokens(text: str) -> list[str]:
    words = re.split(r"[^\wäöüß]+", nfc(text).casefold())
    return [w for w in words if w and w not in STOPWORDS]


def _token_match(a: str, b: str) -> bool:
    return a == b or (len(a) > 4 and len(b) > 4 and SequenceMatcher(None, a, b).ratio() > 0.85)


def score(title: str, folder: str) -> float:
    """Share of the folder's words found in the course title (fuzzy, typo tolerant)."""
    folder_tokens = _tokens(folder)
    title_tokens = _tokens(title)
    if not folder_tokens or not title_tokens:
        return 0.0
    hits = sum(1 for f in folder_tokens if any(_token_match(f, t) for t in title_tokens))
    return hits / len(folder_tokens)


def propose_target(title: str, folders: list[str]) -> str | None:
    best: tuple[float, float, str] | None = None
    for folder in folders:
        s = score(title, folder)
        if s < 0.6:
            continue
        tie = SequenceMatcher(None, nfc(title).casefold(), nfc(folder).casefold()).ratio()
        if best is None or (s, tie) > best[:2]:
            best = (s, tie, folder)
    return best[2] if best else None


def local_course_folders(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(nfc(p.name) for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


@dataclass(frozen=True)
class CourseChoice:
    id: str
    title: str
    semester: str
    target: str | None  # None: excluded


def detect_default_target(course_dirs: list[Path]) -> str | None:
    """The catch-all folder most course folders share ("Sonstiges", "Misc", ...)."""
    from collections import Counter

    from .suggest import local_category_dirs, local_subdirs

    found: Counter[str] = Counter()
    for course_dir in course_dirs:
        dirs = local_category_dirs(local_subdirs(course_dir, depth=1), "misc")
        if dirs:
            found[dirs[0]] += 1
    return found.most_common(1)[0][0] if found else None


def render_config(
    base_url: str,
    username: str,
    root: str,
    courses: list[CourseChoice],
    default_target: str = "Sonstiges",
    course_folders: tuple[str, ...] = DEFAULT_COURSE_FOLDERS,
) -> str:
    lines = [
        "# studip-router configuration - see config.example.toml for all options.",
        "# The password is stored in your OS keychain, never in this file.",
        "",
        "[server]",
        f"base_url = {value(base_url)}",
        f"username = {value(username)}",
        "",
        "[sync]",
        f"root = {value(root)}",
        'semester = "current"',
        f"default_target = {value(default_target)}  # where files go that no rule matches",
        "# Subfolders of every course folder the setup or `c` creates:",
        "course_folders = [" + ", ".join(value(f) for f in course_folders) + "]",
        "create_missing_dirs = false",
        "",
        "# Courses are keyed by their Stud.IP ID; `name` is only a label.",
        "# Add [[courses.<id>.rules]] blocks (or run `studip rules suggest`) to route files.",
    ]
    for c in courses:
        lines += ["", f"[courses.{key(c.id)}]", f"name = {value(c.title)}  # {c.semester}"]
        if c.target is None:
            lines.append("exclude = true")
        else:
            lines.append(f"target = {value(c.target)}")
    return "\n".join(lines) + "\n"


# -- where are the course folders? ------------------------------------------------------

# Folder names that hint at study material, used when no course folder exists yet.
ROOT_HINTS = ("uni", "studium", "studies", "university", "hochschule", "semester", "vorlesung")
_SKIP = {"library", "applications", "node_modules", "site-packages", "venv", "pictures", "music"}
MAX_DIRS = 4000  # bound the search: setup must stay fast on any machine


def search_bases(home: Path) -> list[Path]:
    """Places people keep their files, cloud folders first (they are synced to the phone)."""
    bases = [
        home / "Library" / "Mobile Documents" / "com~apple~CloudDocs",  # iCloud Drive
        *sorted((home / "Library" / "CloudStorage").glob("*")),  # OneDrive, Google Drive, ...
        home / "Documents",
        home / "Dokumente",
        home / "Desktop",
        home / "Schreibtisch",
        *sorted(home.glob("Dropbox*")),
        *sorted(home.glob("OneDrive*")),
        home / "Nextcloud",
        home / "Google Drive",
        home,
    ]
    seen: set[Path] = set()
    result = []
    for base in bases:
        if base.is_dir() and base not in seen:
            seen.add(base)
            result.append(base)
    return result


@dataclass(frozen=True)
class RootCandidate:
    path: Path
    matches: int  # course folders found inside
    hint: bool  # its name sounds like study material

    def label(self, home: Path, total: int) -> str:
        shown = display_path(self.path, home)
        return f"{shown}  ({self.matches} of {total} courses found)" if self.matches else shown


def display_path(path: Path, home: Path) -> str:
    icloud = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    if path == icloud or icloud in path.parents:
        return "iCloud Drive/" + path.relative_to(icloud).as_posix()
    if path == home or home in path.parents:
        return "~/" + path.relative_to(home).as_posix()
    return str(path)


def home_path(path: Path, home: Path) -> str:
    """``~/...`` for paths in the home folder: shorter, and the same on another Mac."""
    if path == home or home in path.parents:
        return "~/" + path.relative_to(home).as_posix() if path != home else "~"
    return str(path)


def _children(directory: Path) -> list[Path]:
    try:
        return sorted(
            p
            for p in directory.iterdir()
            if p.is_dir() and not p.name.startswith(".") and not is_ignored(p.name)
        )
    except OSError:
        return []


def find_roots(course_titles: list[str], home: Path | None = None) -> list[RootCandidate]:
    """Folders that already contain course folders, best first.

    The strongest signal is how many of the user's courses have a folder there;
    a name like "Uni" or "Semester 5" only counts when nothing matches yet.
    """
    home = home or Path.home()
    found: dict[Path, RootCandidate] = {}
    visited = 0
    for base in search_bases(home):
        depth_limit = 1 if base == home else 3
        stack = [(base, 0)]
        while stack and visited < MAX_DIRS:
            directory, depth = stack.pop()
            visited += 1
            children = _children(directory)
            names = [nfc(c.name) for c in children]
            matches = sum(1 for t in course_titles if propose_target(t, names))
            words = nfc(directory.name).casefold()
            hint = any(h in words for h in ROOT_HINTS)
            if (matches or hint) and directory not in found and directory != home:
                found[directory] = RootCandidate(directory, matches, hint)
            if depth < depth_limit:
                stack += [
                    (c, depth + 1)
                    for c in reversed(children)
                    if c.name.casefold() not in _SKIP and not (base == home and c in found)
                ]
    ranked = sorted(
        found.values(), key=lambda c: (-c.matches, not c.hint, len(c.path.parts), str(c.path))
    )
    return ranked[:6]


def new_root(semester: str, home: Path | None = None) -> Path:
    """Where to start from scratch: a semester folder in iCloud Drive or Documents."""
    home = home or Path.home()
    bases = search_bases(home)
    base = next((b for b in bases if b != home), home)
    return base / "Uni" / sanitize_filename(semester.replace("/", "-"))


# -- the folders inside each course -----------------------------------------------------


def detect_course_folders(course_dirs: list[Path]) -> tuple[str, ...] | None:
    """The subfolders most existing course folders share, e.g. Folien, Übungen, Sonstiges."""
    from collections import Counter

    dirs = [d for d in course_dirs if d.is_dir()]
    if not dirs:
        return None
    counts = Counter(nfc(c.name) for d in dirs for c in _children(d))
    needed = max(1, (len(dirs) + 1) // 2)  # in at least half of them
    common = sorted(name for name, n in counts.items() if n >= needed)
    return tuple(common) or None


def course_folder_name(title: str) -> str:
    """Folder name for a course: its title without a short type suffix like "(V)"."""
    name = re.sub(r"\s*\((?:V|Ü|VÜ|V\+Ü|S|P|Ue|VL)\)\s*$", "", title.strip(), flags=re.I)
    return sanitize_filename(name or title)


def create_course_folder(root: Path, name: str, subfolders: tuple[str, ...]) -> Path:
    """Create a course folder with the usual subfolders; existing ones are kept as they are."""
    course_dir = root / name
    new = not course_dir.exists()
    course_dir.mkdir(parents=True, exist_ok=True)
    if new:
        for sub in subfolders:
            (course_dir / sub).mkdir(parents=True, exist_ok=True)
    return course_dir
