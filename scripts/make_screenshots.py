"""Regenerate docs/screenshots/*.svg from the fake Stud.IP used in the tests.

    uv run python scripts/make_screenshots.py

Uses invented data only - never point this at a real account.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

import respx  # noqa: E402
from fake_studip import BASE, COURSE, install  # noqa: E402

OUT = REPO / "docs" / "screenshots"


def prepare() -> None:
    tmp = Path(tempfile.mkdtemp())
    root = tmp / "Uni"
    for sub in ("Sonstiges", "Uebungen", "Skript : Alle Folien", "Klausur/Altklausuren"):
        (root / "Quantum Cooking" / sub).mkdir(parents=True)
    cfg = tmp / "config.toml"
    cfg.write_text(
        f"[server]\nbase_url = '{BASE}'\nusername = 'jdoe'\n[sync]\nroot = '{root}'\n"
        f"[courses.{COURSE}]\nname = 'Quantum Cooking'\ntarget = 'Quantum Cooking'\n"
        f"[[courses.{COURSE}.rules]]\nid = 'sheets'\n"
        "match = { folder = 'Übungsblätter*', name_regex = 'blatt (\\d+)' }\n"
        "target = 'Uebungen'\nrename = 'Uebung_{1:02}{ext}'\n"
    )
    os.environ.update(
        HOME=str(tmp),  # paths show as ~/Uni, the same on every run
        STUDIP_ROUTER_CONFIG=str(cfg),
        STUDIP_ROUTER_STATE_DIR=str(tmp / "state"),
        STUDIP_ROUTER_PASSWORD="fake",
    )


async def shoot() -> None:
    from studip_router.tui import StudipRouterApp

    async def settle(pilot, n: int = 30) -> None:
        for _ in range(n):
            await pilot.pause(0.05)

    with respx.mock:
        install(respx.mock)
        app = StudipRouterApp()
        async with app.run_test(size=(110, 30)) as pilot:
            await settle(pilot, 10)
            app.save_screenshot("menu.svg", str(OUT))
            await pilot.press("p")
            await settle(pilot)
            app.save_screenshot("sync-preview.svg", str(OUT))
            await pilot.press("s")
            await settle(pilot, 5)
            app.save_screenshot("sync-confirm.svg", str(OUT))
            await pilot.press("y")
            await settle(pilot)
            app.save_screenshot("sync-done.svg", str(OUT))
            await pilot.press("escape", "comma")
            await settle(pilot, 5)
            await pilot.press("j", "j")
            await settle(pilot, 3)
            app.save_screenshot("settings.svg", str(OUT))
            await pilot.press("escape", "c")
            await settle(pilot)
            app.save_screenshot("courses.svg", str(OUT))
            await pilot.press("escape", "r")
            await settle(pilot)
            app.save_screenshot("rule-suggestions.svg", str(OUT))


async def shoot_setup() -> None:
    """The first-run setup, with a made-up home folder that already has course folders."""
    from textual.widgets import Input

    from studip_router.tui import StudipRouterApp

    home = Path(tempfile.mkdtemp()) / "home"
    semester = home / "Documents" / "Uni" / "Semester 5"
    for course in ("Quantum Cooking", "Rocket Gardening"):
        for sub in ("Folien", "Übungen", "Klausur", "Sonstiges"):
            (semester / course / sub).mkdir(parents=True)
    os.environ.update(HOME=str(home), STUDIP_ROUTER_CONFIG=str(home / "none.toml"))

    async def settle(pilot, n: int = 30) -> None:
        for _ in range(n):
            await pilot.pause(0.05)

    with respx.mock:
        install(respx.mock)
        app = StudipRouterApp()
        async with app.run_test(size=(110, 30)) as pilot:
            for field, value in (("url", BASE), ("username", "jdoe"), ("password", "pw")):
                app.screen.query_one(f"#{field}", Input).value = value
            await pilot.click("#connect")
            await settle(pilot, 40)
            app.save_screenshot("setup-folders.svg", str(OUT))
            await pilot.click("#next")
            await settle(pilot, 10)
            app.save_screenshot("setup-courses.svg", str(OUT))


if __name__ == "__main__":
    prepare()
    OUT.mkdir(parents=True, exist_ok=True)
    asyncio.run(shoot())
    asyncio.run(shoot_setup())
    print(f"written to {OUT}")
