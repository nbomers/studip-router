"""Apply the current rules to files that were synced before (`studip reorganize`).

Only files tracked in the state are moved, only within their course folder,
never onto an existing file. Every move is journaled so `studip undo` can
revert the last reorganisation. Nothing is ever deleted.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from . import paths
from .config import Config
from .fsutil import exclusive_lock, move_no_clobber, nfc, path_key, with_suffix_number
from .models import RemoteFile
from .planner import build_router
from .postprocess import extracted_instead
from .rename import RenameError
from .state import FileRecord, State

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Move:
    record: FileRecord
    src: Path
    dst: Path
    rule_id: str | None
    by_default: bool
    problem: str = ""  # non-empty: will not be moved


def _remote_from(record: FileRecord) -> RemoteFile:
    return RemoteFile(
        id=record.file_id,
        course_id=record.course_id,
        name=record.remote_name,
        folder_path=record.remote_folder_path,
        size=record.filesize,
        chdate=datetime.fromisoformat(record.chdate),
        mime_type=record.mime_type,
        description=record.description,
        downloadable=True,
    )


def plan_reorganize(cfg: Config, state: State, course_id: str | None = None) -> list[Move]:
    moves: list[Move] = []
    claimed: set[str] = set()
    for cid, course in cfg.courses.items():
        if course.exclude or (course_id and cid != course_id):
            continue
        route_file = build_router(cfg, course)
        course_dir = cfg.course_dir(course)
        for record in sorted(state.for_course(cid).values(), key=lambda r: r.local_path):
            src = Path(record.local_path)
            if extracted_instead(src):
                continue  # only its extracted folder is left; that one is the user's
            try:
                route = route_file(_remote_from(record))
            except RenameError as exc:
                moves.append(Move(record, src, src, None, False, str(exc)))
                continue
            if route.skip:
                continue
            dst = course_dir / route.subdir / route.name
            if record.version > 1:
                dst = with_suffix_number(dst, record.version, "_v{n}")
            if path_key(dst) == path_key(src):
                continue
            problem = ""
            if not src.exists():
                problem = "source missing (moved by hand?)"
            elif dst.exists() or path_key(dst) in claimed:
                problem = "target already exists"
            elif (
                not dst.parent.is_dir()
                and not cfg.create_missing_dirs
                and (
                    path_key(dst.parent) != path_key(course_dir / cfg.course_default_target(course))
                    or " / " in cfg.course_default_target(course)
                )
            ):
                problem = "target folder does not exist"
            if not problem:
                claimed.add(path_key(dst))
            moves.append(Move(record, src, dst, route.rule_id, route.by_default, problem))
    return moves


def pending_moves(cfg: Config, state: State) -> list[Move]:
    """Synced files that the current rules or automatic routing would put elsewhere.

    Only files whose routing changed since they were placed count: a file moved
    by hand while its rule stayed the same was the user's choice, not a hint.
    """
    try:
        moves = plan_reorganize(cfg, state)
    except OSError:
        return []
    return [
        m
        for m in moves
        if not m.problem
        and (m.rule_id != m.record.rule_id or m.by_default != m.record.routed_by_default)
    ]


def apply_reorganize(moves: list[Move], state: State) -> tuple[int, list[tuple[Move, str]]]:
    with exclusive_lock(paths.lock_file()):
        return _apply_reorganize(moves, state)


def _apply_reorganize(moves: list[Move], state: State) -> tuple[int, list[tuple[Move, str]]]:
    run_id = state.start_run("reorganize")
    done = 0
    failed: list[tuple[Move, str]] = []
    for move in moves:
        if move.problem:
            continue
        try:
            if not move.dst.parent.is_dir():
                move.dst.parent.mkdir(parents=True)
                state.journal(run_id, "mkdir", str(move.dst.parent))
            move_no_clobber(move.src, move.dst)
        except OSError as exc:
            failed.append((move, str(exc)))
            continue
        state.journal(run_id, "move", str(move.dst), src=str(move.src))
        state.upsert(
            replace(
                move.record,
                local_path=nfc(str(move.dst)),
                rule_id=move.rule_id,
                routed_by_default=move.by_default,
            )
        )
        done += 1
        log.info("move %s -> %s", move.src, move.dst)
    state.finish_run(run_id, {"moved": done, "failed": len(failed)})
    return done, failed


@dataclass(frozen=True)
class UndoStep:
    op: str
    src: str | None
    dst: str
    problem: str = ""


def plan_undo(state: State) -> tuple[int | None, list[UndoStep]]:
    """Steps to revert the most recent reorganize run (newest first)."""
    runs = state.runs(100)
    undone = {
        json.loads(r["summary"]).get("of_run") for r in runs if r["mode"] == "undo" and r["summary"]
    }
    run = next((r for r in runs if r["mode"] == "reorganize" and r["run_id"] not in undone), None)
    if run is None:
        return None, []
    steps = []
    for entry in reversed(state.journal_entries(run["run_id"])):
        problem = ""
        if entry["op"] == "move":
            if not Path(entry["dst"]).exists():
                problem = "file is no longer where reorganize put it"
            elif Path(entry["src"]).exists():
                problem = "original location is occupied again"
        steps.append(UndoStep(entry["op"], entry["src"], entry["dst"], problem))
    return run["run_id"], steps


def apply_undo(state: State, run_id: int, steps: list[UndoStep]) -> int:
    with exclusive_lock(paths.lock_file()):
        return _apply_undo(state, run_id, steps)


def _apply_undo(state: State, run_id: int, steps: list[UndoStep]) -> int:
    by_path = {path_key(r.local_path): r for r in state.all()}
    undone = 0
    undo_run = state.start_run("undo")
    for step in steps:
        if step.problem:
            continue
        if step.op == "move" and step.src:
            move_no_clobber(Path(step.dst), Path(step.src))
            record = by_path.get(path_key(step.dst))
            if record:
                state.upsert(replace(record, local_path=nfc(step.src)))
            state.journal(undo_run, "move", step.src, src=step.dst)
            undone += 1
        elif step.op == "mkdir":
            directory = Path(step.dst)
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()  # only empty folders this tool created
    state.finish_run(undo_run, {"undone": undone, "of_run": run_id})
    return undone
