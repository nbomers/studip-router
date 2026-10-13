"""Human-readable output: plan tables, run summary, log file setup."""

from __future__ import annotations

import logging
from collections import Counter
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .executor import Outcome
from .planner import Action, CoursePlan, Kind
from .sync import CourseEntry, CourseStatus, SyncPlan

STYLE = {
    Kind.DOWNLOAD: "green",
    Kind.ADOPT: "cyan",
    Kind.NEW_VERSION: "yellow",
    Kind.OVERWRITE: "yellow",
    Kind.MKDIR: "blue",
    Kind.SKIP: "dim",
    Kind.ERROR: "red",
}


def setup_file_logging(path: Path, verbose: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("studip_sync")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
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
            e.course.id,
            e.course.semester_title,
            e.course.title,
            f"[{colours[status]}]{status.value}[/]",
            e.config.target if e.config and not e.config.exclude else "",
        )
    return table


def plan_table(plan: CoursePlan, verbose: bool) -> Table | None:
    rows = [a for a in plan.actions if verbose or a.kind is not Kind.SKIP]
    if not rows:
        return None
    title = f"{plan.course.name or plan.course.id} -> {plan.course_dir}"
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
        table.add_row(
            f"[{STYLE[a.kind]}]{a.kind.value}[/]", remote, _rel(a.dest, plan.course_dir), note
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
    console.print(f"\n[bold]{header}[/bold]")
    labels = {
        "download": "new",
        "new-version": "new versions (_vN)",
        "overwrite": "overwritten",
        "adopt": "adopted (already local)",
        "mkdir": "folders created",
        "error": "errors",
    }
    for key, value in sorted(counts.items()):
        console.print(f"  {labels.get(key, key)}: {value}")
    if not counts:
        console.print("  nothing to do")

    if sync_plan.unconfigured:
        console.print("\n[yellow]Courses on Stud.IP without config entry (not synced):[/yellow]")
        for c in sync_plan.unconfigured:
            console.print(f"  {c.id}  {c.title}")
    if sync_plan.missing:
        console.print("\n[yellow]Configured courses not found on Stud.IP:[/yellow]")
        for c in sync_plan.missing:
            console.print(f"  {c.id}  {c.name}")

    deleted = [r for p in sync_plan.plans for r in p.remote_deleted]
    if deleted:
        console.print("\n[yellow]Deleted on Stud.IP (kept locally):[/yellow]")
        for r in deleted:
            console.print(f"  {r.local_path}")

    errors: list[tuple[Action, str]] = [
        (a, a.reason) for p in sync_plan.plans for a in p.actions if a.kind is Kind.ERROR
    ]
    if outcome:
        errors += outcome.failed
    if errors:
        console.print("\n[red]Errors:[/red]")
        for a, msg in errors:
            console.print(f"  {a.file.name if a.file else a.dest}: {msg}")
