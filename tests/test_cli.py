from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx
from fake_studip import BASE, COURSE, OTHER, install
from typer.testing import CliRunner

from studip_router import paths
from studip_router.cli import app

runner = CliRunner()


@pytest.fixture
def setup(tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = Path(paths.config_file())
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        f"[server]\nbase_url = '{BASE}'\nusername = 'jdoe'\n"
        f"[sync]\nroot = '{root}'\n"
        f"[courses.{COURSE}]\nname = 'Quantum Cooking'\ntarget = 'Example Course'\n"
        "[[courses." + COURSE + ".rules]]\nid = 'sheets'\n"
        "match = { folder = 'Übungsblätter*', name_regex = 'blatt (\\d+)' }\n"
        "target = 'Uebungen'\nrename = 'Uebung_{1:02}{ext}'\n"
    )
    monkeypatch.setenv("STUDIP_ROUTER_PASSWORD", "test-only")
    return root / "Example Course"


def run(*args: str):
    result = runner.invoke(app, list(args))
    return result


@respx.mock
def test_dry_run_then_sync_then_noop(setup: Path) -> None:
    install(respx.mock)
    dry = run("--json", "sync", "--dry-run")
    assert dry.exit_code == 0, dry.output
    data = json.loads(dry.output)
    dests = sorted(Path(a["dest"]).relative_to(setup).as_posix() for a in data["actions"])
    assert dests == ["Sonstiges/Orga.pdf", "Sonstiges/lecture-01.pdf", "Uebungen/Uebung_03.pdf"]
    assert [c["id"] for c in data["unconfigured_courses"]] == [OTHER]
    assert not (setup / "Uebungen" / "Uebung_03.pdf").exists()

    real = run("sync")
    assert real.exit_code == 0, real.output
    assert (setup / "Uebungen" / "Uebung_03.pdf").read_bytes() == b"%PDF-sheet-three"
    assert "1 new announcement" in real.output

    again = json.loads(run("--json", "sync").output)
    assert again["actions"] == [] and again["summary"] == {"unchanged": 3}


@respx.mock
def test_status_lists_default_routed_and_unconfigured(setup: Path) -> None:
    install(respx.mock)
    run("sync")
    data = json.loads(run("--json", "status").output)
    assert len(data["default_routed"]) == 2
    assert data["unconfigured_courses"][0]["title"] == "Underwater Basket Weaving"


def test_rules_test_offline(setup: Path) -> None:
    out = json.loads(
        run("--json", "rules", "test", "quantum", "Blatt 7.pdf", "--folder", "Übungsblätter").output
    )
    assert out["rule"] == "sheets" and out["target"].endswith("Uebungen/Uebung_07.pdf")
    out = json.loads(run("--json", "rules", "test", "quantum", "x.pdf").output)
    assert out["rule"] == "default"


@respx.mock
def test_rules_suggest_write(setup: Path) -> None:
    install(respx.mock)
    result = run("rules", "suggest", "--write", "--yes")
    assert result.exit_code == 0, result.output
    text = Path(paths.config_file()).read_text()
    assert "slides-folien" in text and "Skript : Alle Folien" in text
    assert "folder = 'Übungsblätter*'" not in text.split("# ---")[1]  # already covered


@respx.mock
def test_news_marks_read(setup: Path) -> None:
    install(respx.mock)
    first = run("news")
    assert "Room change" in first.output and "room 42" in first.output
    assert "No unread" in run("news").output


def test_invalid_config_exit_code(setup: Path) -> None:
    Path(paths.config_file()).write_text("[server]\nbase_url = 'ftp://x'\n")
    result = run("config", "validate")
    assert result.exit_code == 2
    assert "server.base_url" in result.output


def test_profiles_are_separate(
    setup: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("STUDIP_ROUTER_CONFIG")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    result = run("--profile", "second", "config", "path")
    assert result.output.strip().endswith("profiles/second.toml")
    assert run("--profile", "../evil", "config", "path").exit_code == 2


def test_doctor_offline(setup: Path) -> None:
    result = run("doctor", "--offline")
    assert "config" in result.output and "course Quantum Cooking" in result.output


def test_config_set(setup: Path) -> None:
    assert run("config", "set", "sync.on_update", "skip").exit_code == 0
    assert run("config", "set", "sync.exclude.max_size_mb", "500").exit_code == 0
    assert run("config", "set", "rename.lowercase", "true").exit_code == 0
    bad = run("config", "set", "sync.on_update", "explode")
    assert bad.exit_code == 2 and "must be one of" in bad.output
    out = json.loads(run("--json", "config", "validate").output)
    assert out["valid"]
    text = Path(paths.config_file()).read_text()
    assert 'on_update = "skip"' in text and "max_size_mb = 500" in text


def test_pdf_password_set_list_remove(setup: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stored: dict[str, str] = {}
    monkeypatch.setattr("studip_router.auth.set_pdf_password", stored.__setitem__)
    monkeypatch.setattr("studip_router.auth.get_pdf_password", stored.get)
    monkeypatch.setattr(
        "studip_router.auth.delete_pdf_password", lambda cid: stored.pop(cid, None) is not None
    )
    monkeypatch.setattr("getpass.getpass", lambda prompt: "course-pw")

    result = run("pdf-password", "set", "quantum")
    assert result.exit_code == 0, result.output
    assert stored == {COURSE: "course-pw"}
    assert json.loads(run("--json", "pdf-password", "list").output) == ["Quantum Cooking"]
    assert "PDF password removed" in run("pdf-password", "remove", "quantum").output
    assert stored == {}
