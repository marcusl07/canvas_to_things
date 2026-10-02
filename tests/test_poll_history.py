from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.poll_history import select_run, validate_state


def run_record(run_id, created, *, status="completed", conclusion="success"):
    return {"databaseId": run_id, "createdAt": created, "status": status, "conclusion": conclusion}


def test_select_latest_success_locally_from_unsorted_unfiltered_runs():
    runs = [
        run_record(1, "2026-07-01T00:00:00Z"),
        run_record(4, "2026-10-02T00:00:00Z", status="in_progress", conclusion=None),
        run_record(3, "2026-10-01T00:00:00Z", conclusion="failure"),
        run_record(2, "2026-09-30T00:00:00Z"),
    ]
    assert select_run(runs) == 2


@pytest.mark.parametrize("runs", [[], [run_record(1, "2026-10-01T00:00:00Z", conclusion="failure")]])
def test_no_success_stops_imports(runs):
    with pytest.raises(ValueError, match="refusing"):
        select_run(runs)


@pytest.mark.parametrize("raw", [
    {}, [], {"notified": []}, {"notified": {"bad": "version"}},
    {"notified": {"123:456:version": "different"}},
    {"notified": {}, "pending": {}},
])
def test_invalid_state_rejected(tmp_path, raw):
    path = tmp_path / "state.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        validate_state(path)


@pytest.mark.parametrize("modern", [False, True])
def test_accepts_current_and_legacy_history(tmp_path, modern):
    notified = {"123:456:2026-10-01T00:00:00Z": "2026-10-01T00:00:00Z"}
    raw = {"notified": notified, "pending": []} if modern else notified
    path = tmp_path / "state.json"
    path.write_text(json.dumps(raw))
    validate_state(path)


def test_missing_state_cli_exits_nonzero(tmp_path):
    result = subprocess.run(
        [sys.executable, "scripts/poll_history.py", str(tmp_path / "missing.json")],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "Imports stopped" in result.stderr


def test_workflow_requires_recovery_before_poller_and_serializes_runs():
    workflow = yaml.safe_load(Path(".github/workflows/poll.yml").read_text())
    assert workflow["concurrency"]["cancel-in-progress"] is False
    steps = workflow["jobs"]["poll"]["steps"]
    lookup = next(step for step in steps if step.get("id") == "get_run_id")
    assert "--status" not in lookup["run"]
    download = next(step for step in steps if step.get("uses", "").startswith("actions/download-artifact"))
    assert not download.get("continue-on-error", False)
    assert "if" not in download
    names = [step["name"] for step in steps]
    assert names.index("Validate recovered history") < names.index("Run poller")
    assert 'echo "{}"' not in "\n".join(step.get("run", "") for step in steps)
