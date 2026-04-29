from __future__ import annotations

import io
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts import enable_local_sync_apply, setup_local_sync


def read_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def read_plist(path: Path) -> dict:
    with path.open("rb") as handle:
        return plistlib.load(handle)


def ensure_repo_root(path: Path) -> None:
    (path / "src" / "canvas_things").mkdir(parents=True, exist_ok=True)


@pytest.fixture
def recorded_commands(monkeypatch) -> list[tuple[tuple[str, ...], dict | None]]:
    calls: list[tuple[tuple[str, ...], dict | None]] = []

    def fake_run_command(argv, *, check=True, env=None):
        calls.append((tuple(argv), dict(env) if env is not None else None))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(setup_local_sync, "run_command", fake_run_command)
    monkeypatch.setattr(enable_local_sync_apply, "run_command", fake_run_command)
    return calls


def test_install_local_sync_launch_agent_seeds_missing_config_and_uses_dry_run(
    tmp_path: Path,
    recorded_commands,
):
    example_config = tmp_path / "config.example.yml"
    example_config.write_text(
        """
version: 1
canvas:
  base_url: "https://canvas.example.com"
local_sync:
  mode: "apply"
  candidate_cap: 50
""",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yml"
    launch_agent_path = tmp_path / "LaunchAgents" / "com.canvas_to_things.local_sync.plist"
    stdout_log_path = tmp_path / "logs" / "local_sync.out.log"
    stderr_log_path = tmp_path / "logs" / "local_sync.err.log"

    result = setup_local_sync.install_local_sync_launch_agent(
        config_path=config_path,
        example_config_path=example_config,
        launch_agent_path=launch_agent_path,
        interval_seconds=900,
        repo_root=tmp_path,
        stdout_log_path=stdout_log_path,
        stderr_log_path=stderr_log_path,
    )

    config = read_yaml(config_path)
    plist = read_plist(launch_agent_path)

    assert result["config_created"] is True
    assert config["local_sync"]["mode"] == "dry-run"
    assert "canvas_things.local_sync_if_due_main" in plist["ProgramArguments"]
    assert "--sync-interval-seconds" in plist["ProgramArguments"]
    assert plist["ProgramArguments"][plist["ProgramArguments"].index("--sync-interval-seconds") + 1] == "900"
    assert plist["ProgramArguments"][-1] == "--dry-run"
    assert plist["StartInterval"] == 300
    assert plist["EnvironmentVariables"]["PYTHONPATH"].startswith(str((tmp_path / "src").resolve()))
    assert recorded_commands == [
        (("launchctl", "bootout", f"gui/{setup_local_sync.os.getuid()}", str(launch_agent_path)), None),
        (("launchctl", "bootstrap", f"gui/{setup_local_sync.os.getuid()}", str(launch_agent_path)), None),
    ]


def test_install_local_sync_launch_agent_forces_existing_config_back_to_dry_run(
    tmp_path: Path,
    recorded_commands,
):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        """
version: 1
local_sync:
  project: "School"
  mode: "apply"
""",
        encoding="utf-8",
    )
    example_config = tmp_path / "config.example.yml"
    example_config.write_text("version: 1\n", encoding="utf-8")
    launch_agent_path = tmp_path / "agent.plist"
    stdout_log_path = tmp_path / "logs" / "local_sync.out.log"
    stderr_log_path = tmp_path / "logs" / "local_sync.err.log"

    setup_local_sync.install_local_sync_launch_agent(
        config_path=config_path,
        example_config_path=example_config,
        launch_agent_path=launch_agent_path,
        repo_root=tmp_path,
        stdout_log_path=stdout_log_path,
        stderr_log_path=stderr_log_path,
    )

    config = read_yaml(config_path)
    plist = read_plist(launch_agent_path)

    assert config["local_sync"]["project"] == "School"
    assert config["local_sync"]["mode"] == "dry-run"
    assert "--apply" not in plist["ProgramArguments"]
    assert plist["ProgramArguments"][-1] == "--dry-run"
    assert plist["StartInterval"] == 300


def test_install_local_sync_launch_agent_prompt_uses_inbox_defaults(
    tmp_path: Path,
    recorded_commands,
):
    example_config = tmp_path / "config.example.yml"
    example_config.write_text(
        """
version: 1
local_sync:
  candidate_cap: 200
  timeout_seconds: 120
""",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yml"
    launch_agent_path = tmp_path / "agent.plist"
    prompt_output = io.StringIO()
    responses = iter(["", ""])

    setup_local_sync.install_local_sync_launch_agent(
        config_path=config_path,
        example_config_path=example_config,
        launch_agent_path=launch_agent_path,
        repo_root=tmp_path,
        stdout_log_path=tmp_path / "logs" / "local_sync.out.log",
        stderr_log_path=tmp_path / "logs" / "local_sync.err.log",
        prompt_user=True,
        input_fn=lambda prompt: next(responses),
        output=prompt_output,
    )

    config = read_yaml(config_path)

    assert config["local_sync"]["project"] is None
    assert config["local_sync"]["move_to_project"] is None
    assert config["local_sync"]["mode"] == "dry-run"
    assert "Local sync setup" in prompt_output.getvalue()


def test_install_local_sync_launch_agent_prompt_updates_project_scope_and_move_target(
    tmp_path: Path,
    recorded_commands,
):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        """
version: 1
local_sync:
  project: "School"
  move_to_project: "Deadlines"
  mode: "apply"
""",
        encoding="utf-8",
    )
    example_config = tmp_path / "config.example.yml"
    example_config.write_text("version: 1\n", encoding="utf-8")
    launch_agent_path = tmp_path / "agent.plist"
    responses = iter(["", "", ""])

    setup_local_sync.install_local_sync_launch_agent(
        config_path=config_path,
        example_config_path=example_config,
        launch_agent_path=launch_agent_path,
        repo_root=tmp_path,
        stdout_log_path=tmp_path / "logs" / "local_sync.out.log",
        stderr_log_path=tmp_path / "logs" / "local_sync.err.log",
        prompt_user=True,
        input_fn=lambda prompt: next(responses),
        output=io.StringIO(),
    )

    config = read_yaml(config_path)

    assert config["local_sync"]["project"] == "School"
    assert config["local_sync"]["move_to_project"] == "Deadlines"
    assert config["local_sync"]["mode"] == "dry-run"


def test_enable_automatic_writes_switches_launch_agent_and_runs_one_apply_sync(
    tmp_path: Path,
    recorded_commands,
):
    ensure_repo_root(tmp_path)
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        """
version: 1
local_sync:
  mode: "dry-run"
""",
        encoding="utf-8",
    )
    launch_agent_path = tmp_path / "agent.plist"
    dry_run_payload = setup_local_sync.build_launch_agent_plist(
        config_path=config_path,
        mode="dry-run",
        repo_root=tmp_path,
        interval_seconds=86400,
        stdout_log_path=tmp_path / "logs" / "local_sync.out.log",
        stderr_log_path=tmp_path / "logs" / "local_sync.err.log",
    )
    setup_local_sync.write_launch_agent_plist(launch_agent_path, dry_run_payload)

    enable_local_sync_apply.enable_automatic_writes(
        config_path=config_path,
        launch_agent_path=launch_agent_path,
    )

    config = read_yaml(config_path)
    plist = read_plist(launch_agent_path)

    assert config["local_sync"]["mode"] == "apply"
    assert plist["StartInterval"] == 300
    assert "canvas_things.local_sync_if_due_main" in plist["ProgramArguments"]
    assert "--sync-interval-seconds" in plist["ProgramArguments"]
    assert plist["ProgramArguments"][plist["ProgramArguments"].index("--sync-interval-seconds") + 1] == "86400"
    assert plist["ProgramArguments"][-1] == "--apply"
    assert recorded_commands[0][0][:2] == ("launchctl", "bootout")
    assert recorded_commands[1][0][:2] == ("launchctl", "bootstrap")
    assert recorded_commands[2][0][-1] == "--apply"
    assert "canvas_things.local_sync_main" in recorded_commands[2][0]
    assert recorded_commands[2][1] is not None
    assert recorded_commands[2][1]["PYTHONPATH"].startswith(str((tmp_path / "src").resolve()))
    assert plist["ProgramArguments"][0] == sys.executable


def test_enable_automatic_writes_repairs_stale_interpreter_paths(
    tmp_path: Path,
    recorded_commands,
):
    ensure_repo_root(tmp_path)
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        """
version: 1
local_sync:
  mode: "dry-run"
""",
        encoding="utf-8",
    )
    launch_agent_path = tmp_path / "agent.plist"
    dry_run_payload = setup_local_sync.build_launch_agent_plist(
        config_path=config_path,
        mode="dry-run",
        repo_root=tmp_path,
        interval_seconds=86400,
        stdout_log_path=tmp_path / "logs" / "local_sync.out.log",
        stderr_log_path=tmp_path / "logs" / "local_sync.err.log",
    )
    dry_run_payload["ProgramArguments"][0] = "/missing/venv/bin/python"
    setup_local_sync.write_launch_agent_plist(launch_agent_path, dry_run_payload)

    enable_local_sync_apply.enable_automatic_writes(
        config_path=config_path,
        launch_agent_path=launch_agent_path,
    )

    plist = read_plist(launch_agent_path)

    assert plist["ProgramArguments"][0] == sys.executable
    assert recorded_commands[2][0][0] == sys.executable


def test_enable_automatic_writes_persists_apply_mode_before_immediate_sync_failure(
    tmp_path: Path,
    monkeypatch,
):
    ensure_repo_root(tmp_path)
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        """
version: 1
local_sync:
  mode: "dry-run"
""",
        encoding="utf-8",
    )
    launch_agent_path = tmp_path / "agent.plist"
    dry_run_payload = setup_local_sync.build_launch_agent_plist(
        config_path=config_path,
        mode="dry-run",
        repo_root=tmp_path,
        interval_seconds=86400,
        stdout_log_path=tmp_path / "logs" / "local_sync.out.log",
        stderr_log_path=tmp_path / "logs" / "local_sync.err.log",
    )
    setup_local_sync.write_launch_agent_plist(launch_agent_path, dry_run_payload)

    recorded_calls: list[tuple[tuple[str, ...], dict | None]] = []

    def fake_run_command(argv, *, check=True, env=None):
        recorded_calls.append((tuple(argv), dict(env) if env is not None else None))
        if argv[0] == "launchctl":
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        raise setup_local_sync.LocalSyncSetupError("Command failed: exit code 4")

    monkeypatch.setattr(setup_local_sync, "run_command", fake_run_command)
    monkeypatch.setattr(enable_local_sync_apply, "run_command", fake_run_command)

    with pytest.raises(
        setup_local_sync.LocalSyncSetupError,
        match="Apply mode was enabled, but the immediate apply sync failed:",
    ):
        enable_local_sync_apply.enable_automatic_writes(
            config_path=config_path,
            launch_agent_path=launch_agent_path,
        )

    config = read_yaml(config_path)
    plist = read_plist(launch_agent_path)

    assert config["local_sync"]["mode"] == "apply"
    assert plist["ProgramArguments"][-1] == "--apply"
    assert recorded_calls[0][0][:2] == ("launchctl", "bootout")
    assert recorded_calls[1][0][:2] == ("launchctl", "bootstrap")
    assert recorded_calls[2][0][-1] == "--apply"


def test_enable_automatic_writes_rejects_missing_program_arguments(
    tmp_path: Path,
):
    ensure_repo_root(tmp_path)
    config_path = tmp_path / "config.yml"
    config_path.write_text("version: 1\nlocal_sync:\n  mode: dry-run\n", encoding="utf-8")
    launch_agent_path = tmp_path / "agent.plist"
    payload = setup_local_sync.build_launch_agent_plist(
        config_path=config_path,
        mode="dry-run",
        repo_root=tmp_path,
        stdout_log_path=tmp_path / "logs" / "local_sync.out.log",
        stderr_log_path=tmp_path / "logs" / "local_sync.err.log",
    )
    payload.pop("ProgramArguments")
    setup_local_sync.write_launch_agent_plist(launch_agent_path, payload)

    with pytest.raises(setup_local_sync.LocalSyncSetupError, match="missing ProgramArguments"):
        enable_local_sync_apply.enable_automatic_writes(
            config_path=config_path,
            launch_agent_path=launch_agent_path,
        )

    assert read_yaml(config_path)["local_sync"]["mode"] == "dry-run"
    assert "ProgramArguments" not in read_plist(launch_agent_path)


def test_enable_automatic_writes_rejects_missing_working_directory(
    tmp_path: Path,
):
    ensure_repo_root(tmp_path)
    config_path = tmp_path / "config.yml"
    config_path.write_text("version: 1\nlocal_sync:\n  mode: dry-run\n", encoding="utf-8")
    launch_agent_path = tmp_path / "agent.plist"
    payload = setup_local_sync.build_launch_agent_plist(
        config_path=config_path,
        mode="dry-run",
        repo_root=tmp_path,
        stdout_log_path=tmp_path / "logs" / "local_sync.out.log",
        stderr_log_path=tmp_path / "logs" / "local_sync.err.log",
    )
    payload.pop("WorkingDirectory")
    setup_local_sync.write_launch_agent_plist(launch_agent_path, payload)

    with pytest.raises(setup_local_sync.LocalSyncSetupError, match="missing WorkingDirectory"):
        enable_local_sync_apply.enable_automatic_writes(
            config_path=config_path,
            launch_agent_path=launch_agent_path,
        )

    assert read_yaml(config_path)["local_sync"]["mode"] == "dry-run"
    assert "WorkingDirectory" not in read_plist(launch_agent_path)


def test_enable_automatic_writes_rejects_non_repo_working_directory(
    tmp_path: Path,
):
    ensure_repo_root(tmp_path)
    config_path = tmp_path / "config.yml"
    config_path.write_text("version: 1\nlocal_sync:\n  mode: dry-run\n", encoding="utf-8")
    launch_agent_path = tmp_path / "agent.plist"
    payload = setup_local_sync.build_launch_agent_plist(
        config_path=config_path,
        mode="dry-run",
        repo_root=tmp_path,
        stdout_log_path=tmp_path / "logs" / "local_sync.out.log",
        stderr_log_path=tmp_path / "logs" / "local_sync.err.log",
    )
    non_repo_path = tmp_path / "not_a_repo"
    non_repo_path.mkdir()
    payload["WorkingDirectory"] = str(non_repo_path)
    setup_local_sync.write_launch_agent_plist(launch_agent_path, payload)

    with pytest.raises(
        setup_local_sync.LocalSyncSetupError,
        match="is not a canvas_to_things repo root",
    ):
        enable_local_sync_apply.enable_automatic_writes(
            config_path=config_path,
            launch_agent_path=launch_agent_path,
        )

    assert read_yaml(config_path)["local_sync"]["mode"] == "dry-run"
    assert read_plist(launch_agent_path)["ProgramArguments"][-1] == "--dry-run"


def test_enable_automatic_writes_stops_before_immediate_sync_when_reload_fails(
    tmp_path: Path,
    monkeypatch,
):
    ensure_repo_root(tmp_path)
    config_path = tmp_path / "config.yml"
    config_path.write_text("version: 1\nlocal_sync:\n  mode: dry-run\n", encoding="utf-8")
    launch_agent_path = tmp_path / "agent.plist"
    dry_run_payload = setup_local_sync.build_launch_agent_plist(
        config_path=config_path,
        mode="dry-run",
        repo_root=tmp_path,
        stdout_log_path=tmp_path / "logs" / "local_sync.out.log",
        stderr_log_path=tmp_path / "logs" / "local_sync.err.log",
    )
    setup_local_sync.write_launch_agent_plist(launch_agent_path, dry_run_payload)

    def fake_reload_launch_agent(path):
        raise setup_local_sync.LocalSyncSetupError(f"Reload failed for {path}")

    monkeypatch.setattr(enable_local_sync_apply, "reload_launch_agent", fake_reload_launch_agent)

    with pytest.raises(setup_local_sync.LocalSyncSetupError, match="Reload failed"):
        enable_local_sync_apply.enable_automatic_writes(
            config_path=config_path,
            launch_agent_path=launch_agent_path,
        )

    assert read_yaml(config_path)["local_sync"]["mode"] == "apply"
    assert read_plist(launch_agent_path)["ProgramArguments"][-1] == "--apply"


def test_enable_local_sync_apply_main_reports_post_enable_sync_failures(
    monkeypatch,
    capsys,
):
    def fake_enable_automatic_writes(*, config_path, launch_agent_path):
        raise setup_local_sync.LocalSyncSetupError(
            "Apply mode was enabled, but the immediate apply sync failed: exit code 4"
        )

    monkeypatch.setattr(enable_local_sync_apply, "enable_automatic_writes", fake_enable_automatic_writes)

    result = enable_local_sync_apply.main([])

    captured = capsys.readouterr()

    assert result == 1
    assert captured.err.strip() == "Apply mode was enabled, but the immediate apply sync failed: exit code 4"


def test_enable_automatic_writes_requires_existing_launch_agent(tmp_path: Path):
    config_path = tmp_path / "config.yml"
    config_path.write_text("version: 1\nlocal_sync:\n  mode: dry-run\n", encoding="utf-8")

    with pytest.raises(setup_local_sync.LocalSyncSetupError, match="Run scripts/setup_local_sync.py first"):
        enable_local_sync_apply.enable_automatic_writes(
            config_path=config_path,
            launch_agent_path=tmp_path / "missing.plist",
        )
