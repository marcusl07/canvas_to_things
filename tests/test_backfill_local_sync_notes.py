from __future__ import annotations

from pathlib import Path

from scripts.backfill_local_sync_notes import collect_note_backfill_candidates, main
from canvas_things.local_sync_applescript import LocalSyncTaskNoteUpdateResult
from canvas_things.local_sync_notes import parse_task_note
from canvas_things.local_sync_things_db import ThingsDiscoveryResult, ThingsScope, ThingsTaskRecord


def build_task(*, uuid: str, title: str, notes: str | None) -> ThingsTaskRecord:
    return ThingsTaskRecord(
        uuid=uuid,
        title=title,
        notes=notes,
        deadline_value=None,
        deadline_date=None,
        project_uuid=None,
        project_title=None,
        heading_uuid=None,
    )


def test_collect_note_backfill_candidates_rewrites_legacy_note_into_canonical_contract():
    legacy_note = "\n".join(
        [
            "Project",
            "Course: CS",
            "Due: 2026-04-18 00:00:00 UTC (2026-04-17 17:00:00 PDT)",
            "Submission: online_upload",
            "",
            "Due: mention this in class",
            "  Canvas: quote the rubric",
        ]
    )

    candidates = collect_note_backfill_candidates(
        (
            build_task(uuid="legacy-1", title="CS: Project", notes=legacy_note),
            build_task(uuid="managed-1", title="Managed", notes="Due: 2026-04-19\nCanvas:"),
            build_task(uuid="manual-1", title="Manual", notes="Buy milk\nDue: soon"),
        )
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.task_id == "legacy-1"
    assert candidate.due_text == "2026-04-17"
    assert "Due At: 2026-04-18 00:00:00 UTC (2026-04-17 17:00:00 PDT)" in candidate.rewritten_note
    assert "- Due: mention this in class" in candidate.rewritten_note
    assert "- Canvas: quote the rubric" in candidate.rewritten_note

    parsed = parse_task_note(candidate.rewritten_note)
    assert parsed.managed is True
    assert parsed.writable is True
    assert parsed.due_text == "2026-04-17"


def test_main_dry_run_reports_candidates_without_writing(monkeypatch, capsys):
    discovery = ThingsDiscoveryResult(
        db_path=Path("/tmp/main.sqlite"),
        scope=ThingsScope(kind="inbox"),
        tasks=(
            build_task(
                uuid="legacy-1",
                title="CS: Project",
                notes="Project\nCourse: CS\nDue: 2026-04-18 00:00:00 UTC (2026-04-17 17:00:00 PDT)",
            ),
        ),
    )

    monkeypatch.setattr("scripts.backfill_local_sync_notes.discover_open_tasks", lambda project, db_path=None: discovery)
    monkeypatch.setattr(
        "scripts.backfill_local_sync_notes.apply_task_note_updates",
        lambda updates: (_ for _ in ()).throw(AssertionError("dry-run should not write")),
    )

    exit_code = main([])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "eligible for note backfill (dry-run)" in captured.out
    assert "Re-run with --apply" in captured.out


def test_main_apply_writes_note_updates_and_reports_summary(monkeypatch, capsys):
    discovery = ThingsDiscoveryResult(
        db_path=Path("/tmp/main.sqlite"),
        scope=ThingsScope(kind="inbox"),
        tasks=(
            build_task(
                uuid="legacy-1",
                title="CS: Project",
                notes="Project\nCourse: CS\nDue: 2026-04-18 00:00:00 UTC (2026-04-17 17:00:00 PDT)",
            ),
        ),
    )

    monkeypatch.setattr("scripts.backfill_local_sync_notes.discover_open_tasks", lambda project, db_path=None: discovery)

    def fake_apply_task_note_updates(updates):
        assert len(updates) == 1
        assert updates[0].task_id == "legacy-1"
        assert "Due At:" in updates[0].note
        assert updates[0].note.endswith("Canvas:")
        return (
            LocalSyncTaskNoteUpdateResult(
                task_id="legacy-1",
                title="CS: Project",
                success=True,
                notes_verified=True,
                notes_attempts=1,
                error=None,
            ),
        )

    monkeypatch.setattr("scripts.backfill_local_sync_notes.apply_task_note_updates", fake_apply_task_note_updates)

    exit_code = main(["--apply"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "eligible for note backfill (apply)" in captured.out
    assert "Backfill note update summary processed=1 successes=1 failures=0" in captured.out
