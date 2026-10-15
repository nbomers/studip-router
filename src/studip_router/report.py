"""Human-readable output: plan tables, run summary, log file setup."""

from __future__ import annotations

import logging
from collections import Counter
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from . import paths
from .dates import Change, ChangeKind
from .executor import Outcome
from .planner import Action, CoursePlan, Kind
from .sync import CourseEntry, CourseStatus, SyncPlan


def esc(value: object) -> str:
    """Text from Stud.IP (or the user's files) inside Rich markup: shown as is.

    Course titles, file names and announcements are written by other people; they
    must never be read as markup ("[/b]" crashes, "[link=...]" fakes a link).
    """
    return escape(str(value))


STYLE = {
    Kind.DOWNLOAD: "green",
    Kind.ADOPT: "cyan",
    Kind.NEW_VERSION: "yellow",
    Kind.OVERWRITE: "yellow",
    Kind.MKDIR: "blue",
    Kind.REFRESH: "dim cyan",
    Kind.MOVE: "cyan",
    Kind.SKIP: "dim",
    Kind.ERROR: "red",
}
SYMBOL = {
    Kind.DOWNLOAD: "✓",
    Kind.ADOPT: "≡",
    Kind.NEW_VERSION: "↻",
    Kind.OVERWRITE: "↻",
    Kind.MKDIR: "+",
    Kind.REFRESH: "·",
    Kind.MOVE: "→",
    Kind.SKIP: "◎",
    Kind.ERROR: "✗",
}


class _PrivateLog(RotatingFileHandler):
    """The log names courses and paths: readable only by the user."""

    def _open(self):
        stream = super()._open()
        paths.private_file(Path(self.baseFilename))
        return stream


def setup_file_logging(path: Path, verbose: bool) -> None:
    """Log to ``path`` (rotated). Calling it again never adds a second handler for
    the same file; a handler for another file is replaced."""
    root = logging.getLogger("studip_router")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for existing in [h for h in root.handlers if isinstance(h, _PrivateLog)]:
        if Path(existing.baseFilename) == path.absolute():
            return
        root.removeHandler(existing)
        existing.close()
    paths.private_dir(path.parent)
    handler = _PrivateLog(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)


def _rel(path: Path | None, base: Path) -> str:
    if path is None:
        return ""
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def courses_table(entries: list[CourseEntry], semester_title: str) -> Table:
    table = Table(title=f"Courses ({semester_title})", title_justify="left")
    table.add_column("ID", style="dim")
    table.add_column("Semester")
    table.add_column("Course")
    table.add_column("Status")
    table.add_column("Target")
    colours = {
        CourseStatus.ACTIVE: "green",
        CourseStatus.EXCLUDED: "dim",
        CourseStatus.UNCONFIGURED: "yellow",
    }
    for e in entries:
        status = e.status
        table.add_row(
            Text(e.course.id),
            Text(e.course.semester_title),
            Text(e.course.title),
            f"[{colours[status]}]{status.value}[/]",
            Text(e.config.target if e.config and not e.config.exclude else ""),
        )
    return table


def plan_table(plan: CoursePlan, verbose: bool) -> Table | None:
    rows = [a for a in plan.actions if verbose or a.kind is not Kind.SKIP]
    if not rows:
        return None
    title = esc(f"{plan.course.name or plan.course.id} -> {plan.course_dir}")
    table = Table(title=title, title_justify="left", show_lines=False)
    table.add_column("Action")
    table.add_column("Stud.IP")
    table.add_column("Local (relative to course folder)")
    table.add_column("Note", style="dim")
    for a in rows:
        remote = f"{a.file.folder_path}/{a.file.name}".lstrip("/") if a.file else ""
        note = a.reason or a.note
        if a.route and a.route.by_default and a.kind is Kind.DOWNLOAD:
            note = (note + "; " if note else "") + "default target"
        elif a.route and a.route.rule_id and a.kind is not Kind.SKIP:
            note = (note + "; " if note else "") + f"rule {a.route.rule_id}"
        if a.unzips:
            note = (note + "; " if note else "") + "+ unzip"
        table.add_row(
            f"[{STYLE[a.kind]}]{a.kind.value}[/]",
            Text(remote),
            Text(_rel(a.dest, plan.course_dir)),
            Text(note),
        )
    return table


def summarize(sync_plan: SyncPlan, outcome: Outcome | None = None) -> Counter:
    counts: Counter = Counter()
    for plan in sync_plan.plans:
        counts["unchanged"] += plan.unchanged
        counts["remote deleted"] += len(plan.remote_deleted)
        for a in plan.actions:
            key = f"skipped ({a.reason})" if a.kind is Kind.SKIP else a.kind.value
            counts[key] += 1
            if a.kind is Kind.DOWNLOAD and a.route and a.route.by_default:
                counts["default-routed"] += 1
    if outcome is not None:
        counts["failed"] = len(outcome.failed)
    return +counts  # drop zeros


def print_summary(
    console: Console, sync_plan: SyncPlan, dry_run: bool, outcome: Outcome | None = None
) -> None:
    counts = summarize(sync_plan, outcome)
    header = "Dry run - nothing was changed" if dry_run else "Sync finished"
    console.print(f"\n[bold]{esc(header)}[/bold]")
    labels = {
        "download": "new",
        "new-version": "new versions (_vN)",
        "overwrite": "overwritten",
        "adopt": "adopted (already local)",
        "mkdir": "folders created",
        "refresh": "details updated (no new content)",
        "rename": "renamed like on Stud.IP",
        "error": "errors",
    }
    for key, value in sorted(counts.items()):
        console.print(f"  {esc(labels.get(key, key))}: {value}")
    if not counts:
        console.print("  nothing to do")

    if sync_plan.unconfigured:
        console.print("\n[yellow]Courses on Stud.IP without config entry (not synced):[/yellow]")
        for c in sync_plan.unconfigured:
            console.print(f"  {esc(c.id)}  {esc(c.title)}")
    if sync_plan.missing:
        console.print("\n[yellow]Configured courses not found on Stud.IP:[/yellow]")
        for missing in sync_plan.missing:
            console.print(f"  {esc(missing.id)}  {esc(missing.name)}")

    if sync_plan.errors:
        console.print("\n[red]Courses that could not be read (the others were synced):[/red]")
        for name, problem in sync_plan.errors:
            console.print(f"  {esc(name)}: {esc(problem)}")
    if sync_plan.warnings:
        console.print(
            f"\n[yellow]{esc(len(sync_plan.warnings))} unexpected record(s) from Stud.IP "
            "were skipped[/yellow] - see the log for details."
        )

    deleted = [r for p in sync_plan.plans for r in p.remote_deleted]
    if deleted:
        console.print("\n[yellow]Deleted on Stud.IP (kept locally, reported once):[/yellow]")
        for r in deleted:
            console.print(f"  {esc(r.local_path)}")

    errors: list[tuple[Action, str]] = [
        (a, a.reason) for p in sync_plan.plans for a in p.actions if a.kind is Kind.ERROR
    ]
    if outcome:
        errors += outcome.failed
    if errors:
        console.print("\n[red]Errors:[/red]")
        for a, msg in errors:
            console.print(f"  {esc(a.file.name if a.file else a.dest)}: {esc(msg)}")


def plan_json(
    sync_plan: SyncPlan, result, dry_run: bool, news: int, date_changes: list | None = None
) -> dict:
    def action(a: Action) -> dict:
        return {
            "action": a.kind.value,
            "course_id": a.course_id,
            "remote": f"{a.file.folder_path}/{a.file.name}".lstrip("/") if a.file else None,
            "file_id": a.file.id if a.file else None,
            "dest": str(a.dest) if a.dest else None,
            "rule": a.route.rule_id if a.route else None,
            "default_routed": bool(a.route and a.route.by_default),
            "reason": a.reason or a.note or None,
        }

    data = {
        "dry_run": dry_run,
        "semester": sync_plan.semester.title if sync_plan.semester else "all",
        "summary": dict(summarize(sync_plan, result.outcome if result else None)),
        "actions": [action(a) for p in sync_plan.plans for a in p.actions],
        "unconfigured_courses": [{"id": c.id, "title": c.title} for c in sync_plan.unconfigured],
        "remote_deleted": [r.local_path for p in sync_plan.plans for r in p.remote_deleted],
        "new_announcements": news,
        "course_errors": [{"course": n, "error": e} for n, e in sync_plan.errors],
        "warnings": sync_plan.warnings,
        "date_changes": [change_json(c) for c in date_changes or []],
    }
    if result is not None:
        data["failed"] = [
            {"file": a.file.name if a.file else None, "error": msg}
            for a, msg in result.outcome.failed
        ]
        data["extracted"] = [str(p) for p in result.outcome.extracted]
        data["unlocked"] = [str(p) for p in result.outcome.unlocked]
        data["still_locked"] = [
            {"file": str(p), "reason": reason} for p, reason in result.outcome.still_locked
        ]
        data["merged"] = [m.output for m in result.merges if m.written]
    return data


def unlocked_lines(outcome: Outcome) -> list[str]:
    """Rich-markup lines about PDF passwords; empty when nothing happened."""
    lines = []
    if outcome.unlocked:
        lines.append(f"[green]✓[/green] {len(outcome.unlocked)} PDF(s) unlocked")
    for path, reason in outcome.still_locked:
        lines.append(f"[yellow]◎[/yellow] still locked: {esc(path.name)} ({esc(reason)})")
    if outcome.still_locked and not outcome.unlocked:
        lines.append(
            "[dim]The password fits none of these PDFs. If every PDF has its own password, "
            "unlocking does not help: `studip pdf-password remove COURSE`.[/dim]"
        )
    return lines


def print_unlocked(console: Console, outcome: Outcome) -> None:
    for line in unlocked_lines(outcome):
        console.print(f"  {line}")


def change_json(change: Change) -> dict:
    d = change.date
    return {
        "course": d.course,
        "kind": str(change.kind),
        "start": d.start.isoformat(),
        "location": d.location,
        "detail": change.detail,
    }


def print_date_changes(console: Console, changes: list[Change]) -> None:
    if not changes:
        return
    console.print("\n[bold]Changed dates[/bold]")
    for change in changes:
        style = "red" if change.kind in (ChangeKind.CANCELLED, ChangeKind.REMOVED) else "yellow"
        console.print(f"  [{style}]●[/{style}] {esc(change.text())}")


def reorganize_hint(count: int) -> str:
    return (
        f"[yellow]{count} synced file(s)[/yellow] are not where your rules put them now "
        "- `studip reorganize --dry-run` shows them, `studip reorganize` moves them (o in the UI)"
    )
