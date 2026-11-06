from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from studip_router import doctor, schedule
from studip_router.cli import app


def test_systemd_environment_preserves_dollars(monkeypatch, tmp_path: Path) -> None:
    config = tmp_path / 'cost$1 % "quote"' / "config.toml"
    monkeypatch.setenv("STUDIP_ROUTER_CONFIG", str(config))
    monkeypatch.setenv("STUDIP_ROUTER_STATE_DIR", str(tmp_path / "state$2"))
    service, _ = schedule.systemd_units("/home/cost$1/bin/studip", 60, None)
    assert f'Environment="STUDIP_ROUTER_STATE_DIR={tmp_path}/state$2"' in service
    assert 'cost$1 %% \\"quote\\"' in service
    assert 'ExecStart="/home/cost$$1/bin/studip"' in service


def test_systemd_path_cannot_add_unit_lines(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STUDIP_ROUTER_CONFIG", str(tmp_path / "line\nbreak"))
    service, _ = schedule.systemd_units("/bin/studip", 60, None)
    assert "line\\nbreak" in service
    assert not any(line.startswith("break") for line in service.splitlines())


@pytest.fixture
def timer(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(schedule.sys, "platform", "linux")
    monkeypatch.setattr(schedule, "_systemd_dir", lambda: tmp_path)
    path = tmp_path / "studip-router.timer"
    path.write_text("[Timer]\nOnUnitActiveSec=60min\n")
    return path


@pytest.mark.parametrize(
    ("load", "active", "enabled", "running"),
    [
        ("loaded", "active", "enabled", True),
        ("loaded", "inactive", "disabled", False),
        ("loaded", "failed", "enabled", False),
        ("not-found", "inactive", "", False),
    ],
)
def test_status_queries_timer_state(monkeypatch, timer, load, active, enabled, running) -> None:
    calls = []

    def query(args, **kwargs):
        calls.append(args)
        assert kwargs["timeout"] == 5
        return subprocess.CompletedProcess(
            args, 0, f"LoadState={load}\nActiveState={active}\nUnitFileState={enabled}\n"
        )

    monkeypatch.setattr(schedule.subprocess, "run", query)
    result = schedule.status()
    assert result.installed and result.path == timer
    assert result.loaded is running
    assert active in result.detail
    assert calls == [
        [
            "systemctl",
            "--user",
            "show",
            "studip-router.timer",
            "--property=LoadState,ActiveState,UnitFileState",
        ]
    ]
    checks = list(doctor._check_schedule())
    assert checks[0].level == (doctor.Level.OK if running else doctor.Level.WARN)


@pytest.mark.parametrize("failure", ["missing", "timeout", "no-bus"])
def test_status_reports_unavailable_manager(monkeypatch, timer, failure) -> None:
    def query(args, **kwargs):
        if failure == "missing":
            raise FileNotFoundError("systemctl")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 5)
        return subprocess.CompletedProcess(args, 1, "", "Failed to connect to bus")

    monkeypatch.setattr(schedule.subprocess, "run", query)
    result = schedule.status()
    assert result.installed and not result.loaded
    assert "cannot query systemd" in result.detail
    cli = CliRunner().invoke(app, ["schedule", "status"])
    assert cli.exit_code == 0 and "cannot query systemd" in cli.output
    cli_json = CliRunner().invoke(app, ["--json", "schedule", "status"])
    assert json.loads(cli_json.stdout)["loaded"] is False


def test_no_timer_does_not_query_manager(monkeypatch, timer) -> None:
    timer.unlink()

    def unexpected(*args, **kwargs):
        pytest.fail("no installed timer: do not query systemd")

    monkeypatch.setattr(schedule.subprocess, "run", unexpected)
    result = schedule.status()
    assert not result.installed and not result.loaded


@pytest.mark.parametrize("command", ["run-now", "uninstall"])
@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_cli_reports_scheduler_command_failure(monkeypatch, command, platform) -> None:
    monkeypatch.setattr(schedule.sys, "platform", platform)

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["scheduler", "start"])

    monkeypatch.setattr(schedule, "run_now" if command == "run-now" else "uninstall", fail)
    result = CliRunner().invoke(app, ["schedule", command])
    assert result.exit_code == 1
    assert not isinstance(result.exception, subprocess.CalledProcessError)
    assert "Error:" in result.output
    assert "Started." not in result.output


def test_cli_run_now_failure_is_json(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["systemctl", "--user", "start"])

    monkeypatch.setattr(schedule, "run_now", fail)
    result = CliRunner().invoke(app, ["--json", "schedule", "run-now"])
    assert result.exit_code == 1
    assert "error" in json.loads(result.stdout)
