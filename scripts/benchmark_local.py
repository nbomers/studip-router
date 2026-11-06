"""Offline local-planner benchmark using disposable synthetic course data.

Run: uv run python scripts/benchmark_local.py --counts 1000 10000 50000
No credentials, network, real course folders or application state are used.
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import tempfile
import time
import tracemalloc
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from studip_router.config import Config, CourseConfig
from studip_router.models import RemoteFile
from studip_router.planner import plan_course
from studip_router.reorganize import pending_moves
from studip_router.state import FileRecord, State


def measure(count: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="studip-benchmark-") as temporary:
        root = Path(temporary)
        folder = root / "course" / "Sonstiges"
        folder.mkdir(parents=True)
        now = datetime.now(UTC)
        files = []
        records = {}
        for i in range(count):
            name = f"Document-{i:06}.pdf"
            path = folder / name
            path.write_bytes(b"abc")
            stat = path.stat()
            fid = f"f{i}"
            files.append(RemoteFile(fid, "c", name, "", 3, now, "application/pdf", "", True))
            records[fid] = FileRecord(
                fid,
                "c",
                name,
                "",
                now.isoformat(),
                3,
                str(path),
                3,
                stat.st_mtime,
                None,
                synced_at=now.isoformat(),
            )
        course = CourseConfig("c", target="course")
        cfg = Config(
            root / "config.toml",
            "https://example.invalid",
            "synthetic",
            root,
            courses={"c": course},
        )
        with State(root / "state.sqlite") as state:
            values = [asdict(record) for record in records.values()]
            columns = list(values[0])
            state._db.executemany(
                f"INSERT INTO files ({','.join(columns)}) VALUES "
                f"({','.join(':' + key for key in columns)})",
                values,
            )
            state._db.commit()
            tracemalloc.start()
            start = time.perf_counter()
            plan = plan_course(cfg, course, files, records)
            elapsed = time.perf_counter() - start
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            start = time.perf_counter()
            moves = pending_moves(cfg, state)
            move_seconds = time.perf_counter() - start
            start = time.perf_counter()
            counts = state.file_counts()
            count_seconds = time.perf_counter() - start
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if sys.platform == "darwin":
                rss //= 1024
            return {
                "files": count,
                "unchanged": plan.unchanged,
                "moves": len(moves),
                "plan_seconds": round(elapsed, 4),
                "pending_moves_seconds": round(move_seconds, 4),
                "count_seconds": round(count_seconds, 6),
                "tracked": counts[0],
                "planner_peak_bytes": peak,
                "process_peak_rss_kib": rss,
                "http_requests": 0,
            }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--counts", nargs="+", type=int, default=[1000, 10000, 50000])
    args = parser.parse_args()
    if any(count < 1 or count > 100000 for count in args.counts):
        parser.error("counts must be between 1 and 100000")
    print(
        json.dumps(
            {
                "python": sys.version.split()[0],
                "platform": sys.platform,
                "results": [measure(count) for count in args.counts],
            },
            indent=2,
        )
    )
