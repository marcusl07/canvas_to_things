from pathlib import Path

import pytest

from canvas_things.local_sync_cli import (
    args_to_overrides,
    build_parser,
    load_settings_from_argv,
    parse_args,
)


def test_parse_args_reads_scope_mode_and_limits():
    args = parse_args(
        [
            "--config",
            "custom.yml",
            "--project",
            "School",
            "--move-to-project",
            "Deadlines",
            "--apply",
            "--candidate-cap",
            "150",
            "--timeout-seconds",
            "30.5",
        ]
    )

    assert args.config == Path("custom.yml")
    assert args.project == "School"
    assert args.move_to_project == "Deadlines"
    assert args.apply is True
    assert args.dry_run is False
    assert args.candidate_cap == 150
    assert args.timeout_seconds == 30.5


def test_parse_args_rejects_conflicting_mode_flags():
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--dry-run", "--apply"])

    assert exc_info.value.code == 2


def test_args_to_overrides_only_sets_explicit_values():
    args = parse_args(["--project", "School", "--dry-run"])

    overrides = args_to_overrides(args)

    assert overrides.project == "School"
    assert overrides.move_to_project is None
    assert overrides.mode == "dry-run"
    assert overrides.candidate_cap is None
    assert overrides.timeout_seconds is None


def test_load_settings_from_argv_merges_cli_over_file(tmp_path):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        """
version: 1
local_sync:
  project: "Original"
  move_to_project: "Configured Project"
  mode: "dry-run"
  candidate_cap: 99
  timeout_seconds: 45
""",
        encoding="utf-8",
    )

    args, settings = load_settings_from_argv(
        [
            "--config",
            str(config_path),
            "--project",
            "CLI Project",
            "--apply",
            "--timeout-seconds",
            "10",
        ]
    )

    assert args.config == config_path
    assert settings.project == "CLI Project"
    assert settings.move_to_project == "Configured Project"
    assert settings.mode == "apply"
    assert settings.candidate_cap == 99
    assert settings.timeout_seconds == 10.0


def test_build_parser_documents_exit_codes():
    parser = build_parser()

    assert parser.epilog is not None
    assert "Exit codes:" in parser.epilog
    assert "0 = Run completed successfully." in parser.epilog
    assert "5 = The run stopped because the wall-clock timeout was exceeded." in parser.epilog
