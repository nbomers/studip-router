"""Opt-in check for a newer release (roadmap item 5)."""

from __future__ import annotations

import pytest

from studip_router import __version__, new_version


def test_version_comparison() -> None:
    assert new_version.is_newer("99.0.0")
    assert not new_version.is_newer(__version__)
    assert not new_version.is_newer("0.0.1")
    assert not new_version.is_newer("99.0.0rc1")  # no pre-releases
    assert new_version.is_newer("0.10.0", "0.9.9")  # numbers, not text


def test_asks_at_most_once_a_week_and_failures_are_quiet() -> None:
    calls: list[int] = []

    def fetch() -> str:
        calls.append(1)
        return "99.0.0"

    assert new_version.check(fetch=fetch, now=1000.0) == "99.0.0"
    assert new_version.check(fetch=fetch, now=1000.0 + 86400) == "99.0.0"
    assert len(calls) == 1  # cached

    def broken() -> str:
        raise OSError("offline")

    later = 1000.0 + new_version.INTERVAL + 1
    assert new_version.check(fetch=broken, now=later) == "99.0.0"  # old answer kept
    assert "uv tool upgrade" in new_version.hint("99.0.0")


def test_off_by_default_and_never_without_opt_in(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from conftest import make_config

    assert make_config(tmp_path).update_check is False
    monkeypatch.setattr(new_version, "_fetch", lambda: pytest.fail("contacted PyPI"))
    from typer.testing import CliRunner

    from studip_router.cli import app
    from studip_router.paths import config_file

    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"[server]\nbase_url = 'https://studip.example.edu'\nusername = 'j'\n"
        f"[sync]\nroot = '{tmp_path}'\n"
    )
    assert CliRunner().invoke(app, ["status", "--offline"]).exit_code == 0
    path.write_text(path.read_text() + "[ui]\nupdate_check = true\n")
    monkeypatch.setattr(new_version, "_fetch", lambda: "99.0.0")
    result = CliRunner().invoke(app, ["status"])
    assert "99.0.0 is available" in result.output
