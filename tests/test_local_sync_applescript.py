from datetime import date

import pytest

from canvas_things.local_sync_applescript import (
    AppleScriptExecutionResult,
    LocalSyncAppleScriptError,
    LocalSyncProjectTarget,
    LocalSyncTaskNoteUpdate,
    LocalSyncTaskMutation,
    VERIFY_ATTEMPTS,
    VERIFY_DELAY_SECONDS,
    apply_task_note_updates,
    apply_task_mutations,
    build_apply_task_note_updates_script,
    build_apply_task_mutations_script,
    parse_task_note_update_results,
    parse_task_mutation_results,
)


class RecordingRunner:
    def __init__(self, execution: AppleScriptExecutionResult) -> None:
        self.execution = execution
        self.calls: list[str] = []

    def __call__(self, script: str) -> AppleScriptExecutionResult:
        self.calls.append(script)
        return self.execution


def test_apply_task_mutations_runs_one_batch_and_parses_partial_failures():
    mutations = (
        LocalSyncTaskMutation(
            task_id="task-1",
            title="Essay draft",
            update_due_date=True,
            due_date=date(2026, 4, 18),
            project_target=LocalSyncProjectTarget(name="School"),
        ),
        LocalSyncTaskMutation(
            task_id="task-2",
            title="Quiz reminder",
            trash=True,
        ),
    )
    runner = RecordingRunner(
        AppleScriptExecutionResult(
            returncode=0,
            stdout="""
[
  {
    "task_id": "task-1",
    "title": "Essay draft",
    "success": true,
    "due_date_verified": true,
    "due_date_attempts": 1,
    "project_verified": true,
    "project_attempts": 2,
    "trash_verified": false,
    "trash_attempts": 0,
    "error": null
  },
  {
    "task_id": "task-2",
    "title": "Quiz reminder",
    "success": false,
    "due_date_verified": false,
    "due_date_attempts": 0,
    "project_verified": false,
    "project_attempts": 0,
    "trash_verified": false,
    "trash_attempts": 3,
    "error": "Failed to verify trash move after 3 attempts. (-2700)"
  }
]
""",
            stderr="",
        )
    )

    results = apply_task_mutations(mutations, runner=runner)

    assert len(runner.calls) == 1
    assert results[0].success is True
    assert results[0].due_date_attempts == 1
    assert results[0].project_attempts == 2
    assert results[1].success is False
    assert results[1].trash_attempts == 3
    assert "trash move" in results[1].error


def test_apply_task_note_updates_runs_one_batch_and_parses_results():
    note_updates = (
        LocalSyncTaskNoteUpdate(
            task_id="task-1",
            title="Essay draft",
            note="Project\nCourse: CS\nDue: 2026-04-18\nDue At: 2026-04-18 00:00:00 UTC\n\nCanvas:",
        ),
    )
    runner = RecordingRunner(
        AppleScriptExecutionResult(
            returncode=0,
            stdout="""
[
  {
    "task_id": "task-1",
    "title": "Essay draft",
    "success": true,
    "notes_verified": true,
    "notes_attempts": 2,
    "error": null
  }
]
""",
            stderr="",
        )
    )

    results = apply_task_note_updates(note_updates, runner=runner)

    assert len(runner.calls) == 1
    assert results[0].success is True
    assert results[0].notes_verified is True
    assert results[0].notes_attempts == 2


def test_apply_task_mutations_skips_runner_for_empty_batch():
    runner = RecordingRunner(AppleScriptExecutionResult(returncode=0, stdout="[]", stderr=""))

    assert apply_task_mutations((), runner=runner) == ()
    assert runner.calls == []


def test_apply_task_note_updates_skips_runner_for_empty_batch():
    runner = RecordingRunner(AppleScriptExecutionResult(returncode=0, stdout="[]", stderr=""))

    assert apply_task_note_updates((), runner=runner) == ()
    assert runner.calls == []


def test_apply_task_mutations_raises_for_process_failure():
    mutation = LocalSyncTaskMutation(task_id="task-1", title="Essay", trash=True)
    runner = RecordingRunner(
        AppleScriptExecutionResult(
            returncode=1,
            stdout="",
            stderr="Things got an error: Not authorized.",
        )
    )

    with pytest.raises(LocalSyncAppleScriptError, match="Not authorized"):
        apply_task_mutations((mutation,), runner=runner)


def test_apply_task_note_updates_raises_for_process_failure():
    note_update = LocalSyncTaskNoteUpdate(task_id="task-1", title="Essay", note="Due: 2026-04-18\nCanvas:")
    runner = RecordingRunner(
        AppleScriptExecutionResult(
            returncode=1,
            stdout="",
            stderr="Things got an error: Not authorized.",
        )
    )

    with pytest.raises(LocalSyncAppleScriptError, match="Not authorized"):
        apply_task_note_updates((note_update,), runner=runner)


def test_build_apply_task_mutations_script_includes_retry_delay_and_uuid_trash_verification():
    script = build_apply_task_mutations_script(
        (
            LocalSyncTaskMutation(
                task_id='task-"1"',
                title='Essay "draft"',
                update_due_date=True,
                due_date=date(2026, 4, 18),
                project_target=LocalSyncProjectTarget(project_id="project-1"),
                trash=True,
            ),
            LocalSyncTaskMutation(
                task_id="task-2",
                title="Inbox item",
                move_to_inbox=True,
            ),
        )
    )

    assert f"repeat with attemptIndex from 1 to {VERIFY_ATTEMPTS}" in script
    assert f"delay {VERIFY_DELAY_SECONDS}" in script
    assert 'set trashMatches to (every to do of list "Trash" whose id is "task-\\"1\\"")' in script
    assert 'set taskRef to (to do id "task-\\"1\\"")' in script
    assert 'set targetProject to (project id "project-1")' in script
    assert "set project of taskRef to targetProject" in script
    assert "move taskRef to targetProject" not in script
    assert 'set due date of taskRef to date "4/18/2026 00:00:00"' in script
    assert 'set inboxMatches to (every to do of list "Inbox" whose id is "task-2")' in script


def test_build_apply_task_note_updates_script_handles_multiline_notes():
    script = build_apply_task_note_updates_script(
        (
            LocalSyncTaskNoteUpdate(
                task_id='task-"1"',
                title='Essay "draft"',
                note='Project "One"\nCourse: CS\nDue: 2026-04-18\n\nCanvas:',
            ),
        )
    )

    assert f"repeat with attemptIndex from 1 to {VERIFY_ATTEMPTS}" in script
    assert f"delay {VERIFY_DELAY_SECONDS}" in script
    assert 'set taskRef to (to do id "task-\\"1\\"")' in script
    assert 'set expectedNoteText to my normalizeNewlines("Project \\"One\\"" & linefeed & "Course: CS"' in script
    assert "set notes of taskRef to expectedNoteText" in script
    assert "set actualNoteText to my normalizeNewlines(notes of taskRef as text)" in script


def test_parse_task_mutation_results_rejects_missing_or_misordered_results():
    expected = (
        LocalSyncTaskMutation(task_id="task-1", title="One", trash=True),
        LocalSyncTaskMutation(task_id="task-2", title="Two", trash=True),
    )

    with pytest.raises(LocalSyncAppleScriptError, match="returned 1 task results"):
        parse_task_mutation_results(
            """
            [
              {
                "task_id": "task-1",
                "title": "One",
                "success": true,
                "due_date_verified": false,
                "due_date_attempts": 0,
                "project_verified": false,
                "project_attempts": 0,
                "trash_verified": true,
                "trash_attempts": 1,
                "error": null
              }
            ]
            """,
            expected_mutations=expected,
        )


def test_parse_task_note_update_results_rejects_missing_or_misordered_results():
    expected = (
        LocalSyncTaskNoteUpdate(task_id="task-1", title="One", note="Due: 2026-04-18\nCanvas:"),
        LocalSyncTaskNoteUpdate(task_id="task-2", title="Two", note="Due: 2026-04-19\nCanvas:"),
    )

    with pytest.raises(LocalSyncAppleScriptError, match="returned 1 task results"):
        parse_task_note_update_results(
            """
            [
              {
                "task_id": "task-1",
                "title": "One",
                "success": true,
                "notes_verified": true,
                "notes_attempts": 1,
                "error": null
              }
            ]
            """,
            expected_note_updates=expected,
        )

    with pytest.raises(LocalSyncAppleScriptError, match="mutation order"):
        parse_task_note_update_results(
            """
            [
              {
                "task_id": "task-2",
                "title": "Two",
                "success": true,
                "notes_verified": true,
                "notes_attempts": 1,
                "error": null
              },
              {
                "task_id": "task-1",
                "title": "One",
                "success": true,
                "notes_verified": true,
                "notes_attempts": 1,
                "error": null
              }
            ]
            """,
            expected_note_updates=expected,
        )

    with pytest.raises(LocalSyncAppleScriptError, match="mutation order"):
        parse_task_mutation_results(
            """
            [
              {
                "task_id": "task-2",
                "title": "Two",
                "success": true,
                "due_date_verified": false,
                "due_date_attempts": 0,
                "project_verified": false,
                "project_attempts": 0,
                "trash_verified": true,
                "trash_attempts": 1,
                "error": null
              },
              {
                "task_id": "task-1",
                "title": "One",
                "success": true,
                "due_date_verified": false,
                "due_date_attempts": 0,
                "project_verified": false,
                "project_attempts": 0,
                "trash_verified": true,
                "trash_attempts": 1,
                "error": null
              }
            ]
            """,
            expected_mutations=expected,
        )


def test_parse_task_mutation_results_rejects_invalid_attempt_counts():
    expected = (LocalSyncTaskMutation(task_id="task-1", title="One", trash=True),)

    with pytest.raises(LocalSyncAppleScriptError, match="due_date_attempts"):
        parse_task_mutation_results(
            """
            [
              {
                "task_id": "task-1",
                "title": "One",
                "success": true,
                "due_date_verified": false,
                "due_date_attempts": 4,
                "project_verified": false,
                "project_attempts": 0,
                "trash_verified": true,
                "trash_attempts": 1,
                "error": null
              }
            ]
            """,
            expected_mutations=expected,
        )


def test_parse_task_note_update_results_rejects_invalid_attempt_counts():
    expected = (LocalSyncTaskNoteUpdate(task_id="task-1", title="One", note="Due: 2026-04-18\nCanvas:"),)

    with pytest.raises(LocalSyncAppleScriptError, match="notes_attempts"):
        parse_task_note_update_results(
            """
            [
              {
                "task_id": "task-1",
                "title": "One",
                "success": true,
                "notes_verified": true,
                "notes_attempts": 4,
                "error": null
              }
            ]
            """,
            expected_note_updates=expected,
        )


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"task_id": " ", "title": "Essay", "trash": True}, "task_id"),
        ({"task_id": "task-1", "title": " ", "trash": True}, "title"),
        ({"task_id": "task-1", "title": "Essay"}, "at least one mutation"),
        (
            {"task_id": "task-1", "title": "Essay", "due_date": date(2026, 4, 18), "trash": True},
            "update_due_date",
        ),
        (
            {
                "task_id": "task-1",
                "title": "Essay",
                "project_target": LocalSyncProjectTarget(name="School"),
                "move_to_inbox": True,
            },
            "cannot both",
        ),
    ],
)
def test_local_sync_task_mutation_validates_inputs(kwargs, match):
    with pytest.raises(ValueError, match=match):
        LocalSyncTaskMutation(**kwargs)


def test_local_sync_task_note_update_validates_inputs():
    with pytest.raises(ValueError, match="task_id"):
        LocalSyncTaskNoteUpdate(task_id=" ", title="Essay", note="Due: 2026-04-18\nCanvas:")

    with pytest.raises(ValueError, match="title"):
        LocalSyncTaskNoteUpdate(task_id="task-1", title=" ", note="Due: 2026-04-18\nCanvas:")

    with pytest.raises(ValueError, match="note must be a string"):
        LocalSyncTaskNoteUpdate(task_id="task-1", title="Essay", note=None)  # type: ignore[arg-type]


def test_local_sync_project_target_requires_name_or_id():
    with pytest.raises(ValueError, match="requires a project name or project_id"):
        LocalSyncProjectTarget()
