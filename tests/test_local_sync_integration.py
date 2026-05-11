from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from typing import Callable, Sequence

import pytest

from canvas_things import config
from canvas_things.canvas_client import Assignment
from canvas_things.local_sync_applescript import (
    AppleScriptExecutionResult,
    LocalSyncTaskMutation,
    VERIFY_ATTEMPTS,
    VERIFY_DELAY_SECONDS,
    apply_task_mutations as real_apply_task_mutations,
)
from canvas_things.local_sync_planner import (
    CLASSIFICATION_CANONICAL,
    CLASSIFICATION_REDUNDANT_UPDATE,
    build_local_sync_write_plan,
)
from canvas_things.local_sync_main import main
from canvas_things.local_sync_runtime import LocalSyncTimeoutError
from canvas_things.local_sync_things_db import (
    HEADING_TYPE,
    PROJECT_TYPE,
    TASK_TYPE,
    ThingsTaskRecord,
    discover_open_tasks as real_discover_open_tasks,
)
from canvas_things.notifier import Notifier

LOCAL_SYNC_TODAY = date(2026, 4, 1)


class ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


class FakeTimeoutGuard:
    def __init__(self, *, error_on_step: str | None = None) -> None:
        self.pre_apply_calls = 0
        self.step_calls: list[str | None] = []
        self.raise_pre_apply = False
        self.error_on_step = error_on_step

    def check_pre_apply(self) -> None:
        self.pre_apply_calls += 1
        if self.raise_pre_apply:
            raise LocalSyncTimeoutError("Local sync timeout exceeded before apply after 10.0s (limit: 10.0s).")

    def check_result_step_boundary(self, step_name: str | None = None) -> None:
        self.step_calls.append(step_name)
        if self.error_on_step == step_name:
            raise LocalSyncTimeoutError(
                f"Local sync timeout exceeded between task result steps ({step_name}) after 10.0s (limit: 10.0s)."
            )


def build_logger(name: str) -> tuple[logging.Logger, ListHandler]:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = ListHandler()
    logger.addHandler(handler)
    return logger, handler


def write_config(
    tmp_path: Path,
    *,
    version: int = 1,
    project: str | None = None,
    move_to_project: str | None = None,
    mode: str = "dry-run",
    candidate_cap: int | None = None,
    timeout_seconds: float | None = None,
) -> Path:
    lines = [f"version: {version}", "local_sync:"]
    if project is not None:
        lines.append(f'  project: "{project}"')
    if move_to_project is not None:
        lines.append(f'  move_to_project: "{move_to_project}"')
    lines.append(f'  mode: "{mode}"')
    if candidate_cap is not None:
        lines.append(f"  candidate_cap: {candidate_cap}")
    if timeout_seconds is not None:
        lines.append(f"  timeout_seconds: {timeout_seconds}")

    config_path = tmp_path / "config.yml"
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_path


def create_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "main.sqlite"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE TMTask (
                uuid TEXT PRIMARY KEY,
                type INTEGER,
                status INTEGER,
                trashed INTEGER,
                title TEXT,
                notes TEXT,
                deadline INTEGER,
                activation_date INTEGER,
                project TEXT,
                heading TEXT
            )
            """
        )
        connection.commit()
    finally:
        connection.close()
    return db_path


def insert_task(
    db_path: Path,
    *,
    uuid: str,
    task_type: int,
    title: str,
    notes: str | None = None,
    deadline: int | None = None,
    activation_date: int | None = None,
    project: str | None = None,
    heading: str | None = None,
    status: int = 0,
    trashed: int = 0,
) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO TMTask (uuid, type, status, trashed, title, notes, deadline, activation_date, project, heading)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (uuid, task_type, status, trashed, title, notes, deadline, activation_date, project, heading),
        )
        connection.commit()
    finally:
        connection.close()


def update_task_fields(
    db_path: Path,
    *,
    uuid: str,
    deadline: int | None = None,
    trashed: int | None = None,
) -> None:
    assignments: list[str] = []
    values: list[object] = []
    if deadline is not None:
        assignments.append("deadline = ?")
        values.append(deadline)
    if trashed is not None:
        assignments.append("trashed = ?")
        values.append(trashed)
    if not assignments:
        raise ValueError("update_task_fields requires at least one field update.")

    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            f"UPDATE TMTask SET {', '.join(assignments)} WHERE uuid = ?",
            (*values, uuid),
        )
        connection.commit()
    finally:
        connection.close()


def encode_deadline(deadline_date: date) -> int:
    return (deadline_date.year << 16) | (deadline_date.month << 12) | (deadline_date.day << 7)


def managed_note(due_text: str) -> str:
    return f"Due: {due_text}\nCanvas:"


def notifier_settings(*, include_description: bool = True) -> config.Settings:
    return config.Settings(
        canvas=config.CanvasConfig(base_url="https://canvas.example.com", courses=[]),
        email=config.EmailConfig(
            from_name="Bot",
            subject_template="{course_alias}: {title}",
            include_description=include_description,
            max_description_chars=500,
        ),
        run=config.RunConfig(timezone="UTC", dry_run=False, state_file=Path("state.json")),
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="bot@example.com",
        smtp_pass="pass",
        things_email="user@things.email",
        canvas_token="token",
    )


def build_notifier_message(
    *,
    title: str,
    due_at: str,
    description: str | None = None,
    is_update_notification: bool = False,
) -> EmailMessage:
    assignment = Assignment(
        course_id=1,
        course_alias="CS",
        assignment_id=5,
        title=title,
        html_url="https://canvas.example.com/a/5",
        updated_at="2025-01-01T00:00:00Z",
        due_at=due_at,
        lock_at=None,
        unlock_at=None,
        description=description,
        points_possible=50.0,
        submission_types=["online_upload"],
        published=True,
        is_update_notification=is_update_notification,
    )
    return Notifier(settings=notifier_settings())._build_message(assignment)


def notifier_task_record(
    *,
    uuid: str,
    title: str,
    due_at: str,
    description: str | None = None,
    is_update_notification: bool = False,
    deadline_date: date | None = None,
    activation_date: date | None = None,
) -> ThingsTaskRecord:
    message = build_notifier_message(
        title=title,
        due_at=due_at,
        description=description,
        is_update_notification=is_update_notification,
    )
    return ThingsTaskRecord(
        uuid=uuid,
        title=str(message["Subject"]),
        notes=message.get_content(),
        deadline_value=None,
        deadline_date=deadline_date,
        activation_date_value=None,
        activation_date=activation_date,
        project_uuid=None,
        project_title=None,
        heading_uuid=None,
    )


def mutation_payload(mutation: LocalSyncTaskMutation, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "task_id": mutation.task_id,
        "title": mutation.title,
        "success": True,
        "due_date_verified": mutation.update_due_date,
        "due_date_attempts": 1 if mutation.update_due_date else 0,
        "schedule_verified": mutation.update_schedule_date,
        "schedule_attempts": 1 if mutation.update_schedule_date else 0,
        "title_verified": mutation.update_title,
        "title_attempts": 1 if mutation.update_title else 0,
        "project_verified": mutation.project_target is not None or mutation.move_to_inbox,
        "project_attempts": 1 if mutation.project_target is not None or mutation.move_to_inbox else 0,
        "trash_verified": mutation.trash,
        "trash_attempts": 1 if mutation.trash else 0,
        "error": None,
    }
    payload.update(overrides)
    return payload


def install_main_integration(
    monkeypatch,
    *,
    db_path: Path,
    logger: logging.Logger,
    execution_builder: Callable[
        [str, Sequence[LocalSyncTaskMutation]],
        AppleScriptExecutionResult,
    ]
    | None = None,
    timeout_guard: FakeTimeoutGuard | None = None,
) -> None:
    @contextmanager
    def fake_lock(config_path: Path):
        yield config_path

    monkeypatch.setattr("canvas_things.local_sync_main.setup_local_sync_logger", lambda **kwargs: logger)
    monkeypatch.setattr("canvas_things.local_sync_main.local_sync_lock", fake_lock)
    monkeypatch.setattr(
        "canvas_things.local_sync_main.build_local_sync_write_plan",
        lambda tasks, **kwargs: build_local_sync_write_plan(
            tasks,
            **kwargs,
            today=LOCAL_SYNC_TODAY,
        ),
    )
    monkeypatch.setattr(
        "canvas_things.local_sync_main.discover_open_tasks",
        lambda project, move_to_project=None: real_discover_open_tasks(
            project,
            move_to_project=move_to_project,
            db_path=db_path,
        ),
    )

    if execution_builder is not None:
        def fake_apply_task_mutations(
            mutations: Sequence[LocalSyncTaskMutation],
        ):
            return real_apply_task_mutations(
                mutations,
                runner=lambda script: execution_builder(script, mutations),
            )

        monkeypatch.setattr("canvas_things.local_sync_main.apply_task_mutations", fake_apply_task_mutations)

    if timeout_guard is not None:
        monkeypatch.setattr("canvas_things.local_sync_main.LocalSyncTimeoutGuard.start", lambda timeout: timeout_guard)


def test_build_local_sync_write_plan_accepts_notifier_emitted_notes_without_manual_edits():
    tasks = (
        notifier_task_record(
            uuid="base",
            title="Essay",
            due_at="2026-04-10T23:59:59Z",
            description="Bring calculator\nDue: mention this in class",
            deadline_date=date(2026, 4, 8),
        ),
        notifier_task_record(
            uuid="update",
            title="Essay",
            due_at="2026-04-12T23:59:59Z",
            description="Canvas: rubric link moved",
            is_update_notification=True,
        ),
    )

    plan = build_local_sync_write_plan(tasks, candidate_cap=200, today=LOCAL_SYNC_TODAY)

    base_entry = next(entry for entry in plan.entries if entry.candidate.task.uuid == "base")
    update_entry = next(entry for entry in plan.entries if entry.candidate.task.uuid == "update")

    assert base_entry.classification == CLASSIFICATION_CANONICAL
    assert base_entry.planned_due_date == date(2026, 4, 12)
    assert base_entry.mutation is not None
    assert base_entry.mutation.update_due_date is True
    assert base_entry.mutation.due_date == date(2026, 4, 12)
    assert base_entry.candidate.parsed_note.writable is True
    assert base_entry.candidate.parsed_note.diagnostics == ()

    assert update_entry.classification == CLASSIFICATION_REDUNDANT_UPDATE
    assert update_entry.mutation is not None
    assert update_entry.mutation.trash is True
    assert update_entry.candidate.parsed_note.writable is True
    assert update_entry.candidate.parsed_note.diagnostics == ()


@pytest.mark.parametrize(
    ("config_text", "message"),
    [
        ("version: 2\nlocal_sync:\n  mode: dry-run\n", "Unsupported local sync config version 2."),
        ("version: 1\nlocal_sync:\n  mode: nope\n", "local_sync.mode must be 'dry-run' or 'apply'."),
    ],
)
def test_main_returns_config_error_for_invalid_config_and_version(tmp_path, capsys, config_text, message):
    config_path = tmp_path / "config.yml"
    config_path.write_text(config_text, encoding="utf-8")

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 2
    assert message in capsys.readouterr().err


def test_main_dry_run_filters_open_inbox_tasks(monkeypatch, tmp_path):
    config_path = write_config(tmp_path, mode="dry-run")
    db_path = create_db(tmp_path)
    logger, handler = build_logger("tests.local_sync_integration.inbox")

    insert_task(db_path, uuid="inbox-heading", task_type=HEADING_TYPE, title="Inbox Heading")
    insert_task(
        db_path,
        uuid="inbox-direct",
        task_type=TASK_TYPE,
        title="Inbox Direct",
        notes=managed_note("2026-04-10"),
    )
    insert_task(
        db_path,
        uuid="inbox-headed",
        task_type=TASK_TYPE,
        title="Inbox Headed",
        notes=managed_note("2026-04-11"),
        heading="inbox-heading",
    )
    insert_task(db_path, uuid="project-row", task_type=PROJECT_TYPE, title="Assignments")
    insert_task(
        db_path,
        uuid="project-task",
        task_type=TASK_TYPE,
        title="Project Task",
        notes=managed_note("2026-04-12"),
        project="project-row",
    )
    insert_task(
        db_path,
        uuid="completed-inbox",
        task_type=TASK_TYPE,
        title="Completed Inbox",
        notes=managed_note("2026-04-13"),
        status=3,
    )
    insert_task(
        db_path,
        uuid="trashed-inbox",
        task_type=TASK_TYPE,
        title="Trashed Inbox",
        notes=managed_note("2026-04-14"),
        trashed=1,
    )

    install_main_integration(monkeypatch, db_path=db_path, logger=logger)

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 0
    assert any(
        "Local sync summary exit_code=0 mode=dry-run scope=Inbox discovered=2 managed=2 writable=2 canonical=2 redundant_updates=0 diagnostics=0 planned_mutations=2"
        in message
        for message in handler.messages
    )


def test_main_dry_run_resolves_named_project_scope(monkeypatch, tmp_path):
    config_path = write_config(tmp_path, mode="dry-run", project="Assignments")
    db_path = create_db(tmp_path)
    logger, handler = build_logger("tests.local_sync_integration.project")

    insert_task(db_path, uuid="project-row", task_type=PROJECT_TYPE, title="Assignments")
    insert_task(db_path, uuid="other-project", task_type=PROJECT_TYPE, title="Other")
    insert_task(db_path, uuid="project-heading", task_type=HEADING_TYPE, title="Queued", project="project-row")
    insert_task(
        db_path,
        uuid="project-direct",
        task_type=TASK_TYPE,
        title="Project Direct",
        notes=managed_note("2026-04-15"),
        project="project-row",
    )
    insert_task(
        db_path,
        uuid="project-headed",
        task_type=TASK_TYPE,
        title="Project Headed",
        notes=managed_note("2026-04-16"),
        heading="project-heading",
    )
    insert_task(
        db_path,
        uuid="other-project-task",
        task_type=TASK_TYPE,
        title="Other Project Task",
        notes=managed_note("2026-04-17"),
        project="other-project",
    )
    insert_task(
        db_path,
        uuid="inbox-task",
        task_type=TASK_TYPE,
        title="Inbox Task",
        notes=managed_note("2026-04-18"),
    )

    install_main_integration(monkeypatch, db_path=db_path, logger=logger)

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 0
    assert any(
        "Local sync summary exit_code=0 mode=dry-run scope=project:Assignments discovered=2 managed=2 writable=2 canonical=2 redundant_updates=0 diagnostics=0 planned_mutations=2"
        in message
        for message in handler.messages
    )


def test_main_returns_precondition_error_for_duplicate_named_project_scope(monkeypatch, tmp_path, capsys):
    config_path = write_config(tmp_path, mode="dry-run", project="Assignments")
    db_path = create_db(tmp_path)
    logger, _ = build_logger("tests.local_sync_integration.duplicate_project")

    insert_task(db_path, uuid="project-1", task_type=PROJECT_TYPE, title="Assignments")
    insert_task(db_path, uuid="project-2", task_type=PROJECT_TYPE, title="Assignments")

    install_main_integration(monkeypatch, db_path=db_path, logger=logger)

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 3
    assert "Duplicate open Things projects named 'Assignments' make scope resolution ambiguous." in capsys.readouterr().err


def test_main_returns_precondition_error_for_missing_move_to_project_scope(monkeypatch, tmp_path, capsys):
    config_path = write_config(tmp_path, mode="dry-run", move_to_project="School")
    db_path = create_db(tmp_path)
    logger, _ = build_logger("tests.local_sync_integration.missing_move_to_project")

    insert_task(db_path, uuid="task-1", task_type=TASK_TYPE, title="Essay", notes=managed_note("2026-04-10"))

    install_main_integration(monkeypatch, db_path=db_path, logger=logger)

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 3
    assert "No open Things project named 'School' was found." in capsys.readouterr().err


def test_main_returns_precondition_error_for_ambiguous_move_to_project_scope(monkeypatch, tmp_path, capsys):
    config_path = write_config(tmp_path, mode="dry-run", move_to_project="School")
    db_path = create_db(tmp_path)
    logger, _ = build_logger("tests.local_sync_integration.ambiguous_move_to_project")

    insert_task(db_path, uuid="project-1", task_type=PROJECT_TYPE, title="School")
    insert_task(db_path, uuid="project-2", task_type=PROJECT_TYPE, title="School")
    insert_task(db_path, uuid="task-1", task_type=TASK_TYPE, title="Essay", notes=managed_note("2026-04-10"))

    install_main_integration(monkeypatch, db_path=db_path, logger=logger)

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 3
    assert "Duplicate open Things projects named 'School' make scope resolution ambiguous." in capsys.readouterr().err


def test_main_returns_precondition_error_for_duplicate_uuid_across_dual_scopes(monkeypatch, tmp_path, capsys):
    config_path = write_config(tmp_path, mode="dry-run", move_to_project="Assignments")
    db_path = create_db(tmp_path)
    logger, _ = build_logger("tests.local_sync_integration.duplicate_uuid_dual_scope")

    insert_task(db_path, uuid="project-row", task_type=PROJECT_TYPE, title="Assignments")
    insert_task(db_path, uuid="inbox-heading", task_type=HEADING_TYPE, title="Inbox Heading")
    insert_task(
        db_path,
        uuid="project-heading",
        task_type=HEADING_TYPE,
        title="Project Heading",
        project="project-row",
        heading="inbox-heading",
    )
    insert_task(
        db_path,
        uuid="shared-task",
        task_type=TASK_TYPE,
        title="Shared Task",
        notes=managed_note("2026-04-10"),
        heading="project-heading",
    )

    install_main_integration(monkeypatch, db_path=db_path, logger=logger)

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 3
    assert "Discovered the same Things task uuid in multiple local-sync discovery scopes: 'shared-task'." in capsys.readouterr().err


def test_main_apply_merges_mixed_inbox_and_destination_project_families_in_canonical_first_order(
    monkeypatch,
    tmp_path,
):
    config_path = write_config(tmp_path, mode="apply", move_to_project="School")
    db_path = create_db(tmp_path)
    logger, handler = build_logger("tests.local_sync_integration.mixed_families")

    insert_task(db_path, uuid="school-project", task_type=PROJECT_TYPE, title="School")
    insert_task(
        db_path,
        uuid="a-project-essay",
        task_type=TASK_TYPE,
        title="Essay",
        notes=managed_note("2026-04-10"),
        deadline=encode_deadline(date(2026, 4, 8)),
        project="school-project",
    )
    insert_task(
        db_path,
        uuid="b-inbox-essay",
        task_type=TASK_TYPE,
        title="Essay",
        notes=managed_note("2026-04-12"),
        deadline=encode_deadline(date(2026, 4, 11)),
    )
    insert_task(
        db_path,
        uuid="c-update-essay",
        task_type=TASK_TYPE,
        title="[UPDATE] Essay",
        notes=managed_note("2026-04-14"),
    )
    insert_task(
        db_path,
        uuid="d-project-quiz",
        task_type=TASK_TYPE,
        title="Quiz",
        notes=managed_note("2026-04-15"),
        deadline=encode_deadline(date(2026, 4, 13)),
        project="school-project",
    )

    def execution_builder(script: str, mutations: Sequence[LocalSyncTaskMutation]) -> AppleScriptExecutionResult:
        assert [mutation.task_id for mutation in mutations] == [
            "a-project-essay",
            "b-inbox-essay",
            "d-project-quiz",
            "c-update-essay",
        ]
        assert mutations[0].project_target is None
        assert mutations[1].project_target is not None
        assert mutations[1].project_target.name == "School"
        assert mutations[2].project_target is None
        assert mutations[3].trash is True
        assert 'set matchingProjects to (every project whose name is "School")' in script
        payload = [mutation_payload(mutation) for mutation in mutations]
        return AppleScriptExecutionResult(returncode=0, stdout=json.dumps(payload), stderr="")

    install_main_integration(
        monkeypatch,
        db_path=db_path,
        logger=logger,
        execution_builder=execution_builder,
    )

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 0
    assert any(
        "Local sync summary exit_code=0 mode=apply scope=Inbox discovered=4 managed=4 writable=4 canonical=3 redundant_updates=1 diagnostics=0 planned_mutations=4 mutation_results=4/4 successes=4 failures=0"
        in message
        for message in handler.messages
    )


def test_main_apply_only_trashes_redundant_updates_and_uses_task_id_for_trash_verification(
    monkeypatch,
    tmp_path,
):
    config_path = write_config(tmp_path, mode="apply")
    db_path = create_db(tmp_path)
    logger, handler = build_logger("tests.local_sync_integration.redundant_updates")

    insert_task(
        db_path,
        uuid="base-1",
        task_type=TASK_TYPE,
        title="Essay",
        notes=managed_note("2026-04-10"),
        deadline=encode_deadline(date(2026, 4, 8)),
    )
    insert_task(
        db_path,
        uuid="base-2",
        task_type=TASK_TYPE,
        title="Essay",
        notes=managed_note("2026-04-11"),
        deadline=encode_deadline(date(2026, 4, 11)),
    )
    insert_task(
        db_path,
        uuid="update-1",
        task_type=TASK_TYPE,
        title="[UPDATE] Essay",
        notes=managed_note("2026-04-12"),
    )

    def execution_builder(script: str, mutations: Sequence[LocalSyncTaskMutation]) -> AppleScriptExecutionResult:
        assert [mutation.task_id for mutation in mutations] == ["base-1", "update-1"]
        assert [mutation.task_id for mutation in mutations if mutation.trash] == ["update-1"]
        assert [mutation.task_id for mutation in mutations if mutation.update_due_date] == ["base-1"]
        assert 'set trashMatches to (every to do of list "Trash" whose id is "update-1")' in script
        payload = [mutation_payload(mutation) for mutation in mutations]
        return AppleScriptExecutionResult(returncode=0, stdout=json.dumps(payload), stderr="")

    install_main_integration(
        monkeypatch,
        db_path=db_path,
        logger=logger,
        execution_builder=execution_builder,
    )

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 0
    assert any(
        "Local sync summary exit_code=0 mode=apply scope=Inbox discovered=3 managed=3 writable=3 canonical=2 redundant_updates=1 diagnostics=0 planned_mutations=2 mutation_results=2/2 successes=2 failures=0"
        in message
        for message in handler.messages
    )


def test_main_apply_accepts_delayed_verification_that_settles_within_retries(monkeypatch, tmp_path):
    config_path = write_config(tmp_path, mode="apply", move_to_project="School")
    db_path = create_db(tmp_path)
    logger, handler = build_logger("tests.local_sync_integration.retry_success")

    insert_task(db_path, uuid="school-project", task_type=PROJECT_TYPE, title="School")
    insert_task(
        db_path,
        uuid="task-1",
        task_type=TASK_TYPE,
        title="Essay Draft",
        notes=managed_note("2026-04-20"),
        deadline=encode_deadline(date(2026, 4, 18)),
    )

    def execution_builder(script: str, mutations: Sequence[LocalSyncTaskMutation]) -> AppleScriptExecutionResult:
        assert [mutation.task_id for mutation in mutations] == ["task-1"]
        assert f"repeat with attemptIndex from 1 to {VERIFY_ATTEMPTS}" in script
        assert f"delay {VERIFY_DELAY_SECONDS}" in script
        assert 'set matchingProjects to (every project whose name is "School")' in script
        assert "set project of taskRef to targetProject" in script
        assert "move taskRef to targetProject" not in script
        payload = [
            mutation_payload(
                mutations[0],
                due_date_attempts=2,
                project_attempts=3,
            )
        ]
        return AppleScriptExecutionResult(returncode=0, stdout=json.dumps(payload), stderr="")

    install_main_integration(
        monkeypatch,
        db_path=db_path,
        logger=logger,
        execution_builder=execution_builder,
    )

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 0
    assert any(
        "Mutation result task_id=task-1 title='Essay Draft' success=True due_attempts=2 project_attempts=3 trash_attempts=0 error=-"
        in message
        for message in handler.messages
    )


def test_main_apply_surfaces_project_write_verification_exhaustion_as_partial_failure(monkeypatch, tmp_path):
    config_path = write_config(tmp_path, mode="apply", move_to_project="School")
    db_path = create_db(tmp_path)
    logger, handler = build_logger("tests.local_sync_integration.project_write_failure")

    insert_task(db_path, uuid="school-project", task_type=PROJECT_TYPE, title="School")
    insert_task(
        db_path,
        uuid="task-1",
        task_type=TASK_TYPE,
        title="Essay Draft",
        notes=managed_note("2026-04-20"),
        deadline=encode_deadline(date(2026, 4, 18)),
    )

    def execution_builder(script: str, mutations: Sequence[LocalSyncTaskMutation]) -> AppleScriptExecutionResult:
        assert [mutation.task_id for mutation in mutations] == ["task-1"]
        assert 'set matchingProjects to (every project whose name is "School")' in script
        assert "set project of taskRef to targetProject" in script
        assert "move taskRef to targetProject" not in script
        payload = [
            mutation_payload(
                mutations[0],
                success=False,
                project_verified=False,
                project_attempts=VERIFY_ATTEMPTS,
                error=f"Failed to verify project write after {VERIFY_ATTEMPTS} attempts. (-2700)",
            )
        ]
        return AppleScriptExecutionResult(returncode=0, stdout=json.dumps(payload), stderr="")

    install_main_integration(
        monkeypatch,
        db_path=db_path,
        logger=logger,
        execution_builder=execution_builder,
    )

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 4
    assert any(
        "Mutation result task_id=task-1 title='Essay Draft' success=False due_attempts=1 project_attempts=3 trash_attempts=0 error=Failed to verify project write after 3 attempts. (-2700)"
        in message
        for message in handler.messages
    )
    assert any("mutation_results=1/1 successes=0 failures=1" in message for message in handler.messages)


def test_main_apply_continues_after_partial_batch_failure(monkeypatch, tmp_path):
    config_path = write_config(tmp_path, mode="apply")
    db_path = create_db(tmp_path)
    logger, handler = build_logger("tests.local_sync_integration.partial_failure")

    insert_task(
        db_path,
        uuid="task-1",
        task_type=TASK_TYPE,
        title="Essay",
        notes=managed_note("2026-04-21"),
        deadline=encode_deadline(date(2026, 4, 18)),
    )
    insert_task(
        db_path,
        uuid="task-2",
        task_type=TASK_TYPE,
        title="Quiz",
        notes=managed_note("2026-04-22"),
        deadline=encode_deadline(date(2026, 4, 18)),
    )

    def execution_builder(script: str, mutations: Sequence[LocalSyncTaskMutation]) -> AppleScriptExecutionResult:
        assert "task-1" in script and "task-2" in script
        payload = [
            mutation_payload(
                mutations[0],
                success=False,
                due_date_verified=False,
                due_date_attempts=3,
                error="Failed to verify due date write after 3 attempts. (-2700)",
            ),
            mutation_payload(mutations[1]),
        ]
        return AppleScriptExecutionResult(returncode=0, stdout=json.dumps(payload), stderr="")

    install_main_integration(
        monkeypatch,
        db_path=db_path,
        logger=logger,
        execution_builder=execution_builder,
    )

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 4
    assert any("Mutation result task_id=task-1" in message for message in handler.messages)
    assert any("Mutation result task_id=task-2" in message for message in handler.messages)
    assert any("mutation_results=2/2 successes=1 failures=1" in message for message in handler.messages)


def test_main_returns_precondition_error_when_candidate_cap_is_exceeded(monkeypatch, tmp_path, capsys):
    config_path = write_config(tmp_path, mode="dry-run", candidate_cap=1)
    db_path = create_db(tmp_path)
    logger, _ = build_logger("tests.local_sync_integration.candidate_cap")

    insert_task(db_path, uuid="task-1", task_type=TASK_TYPE, title="Essay", notes=managed_note("2026-04-10"))
    insert_task(db_path, uuid="task-2", task_type=TASK_TYPE, title="Quiz", notes=managed_note("2026-04-11"))

    install_main_integration(monkeypatch, db_path=db_path, logger=logger)

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 3
    assert "Managed candidate cap exceeded: discovered 2 managed tasks (limit: 1)." in capsys.readouterr().err


def test_main_candidate_cap_counts_diagnostic_only_managed_tasks(monkeypatch, tmp_path, capsys):
    config_path = write_config(tmp_path, mode="dry-run", candidate_cap=1)
    db_path = create_db(tmp_path)
    logger, _ = build_logger("tests.local_sync_integration.candidate_cap_diagnostics")

    insert_task(db_path, uuid="task-1", task_type=TASK_TYPE, title="Essay", notes=managed_note("2026-04-10"))
    insert_task(
        db_path,
        uuid="task-2",
        task_type=TASK_TYPE,
        title="Broken Essay",
        notes="Broken Essay\nDue: tomorrow\nCanvas:\n",
    )

    install_main_integration(monkeypatch, db_path=db_path, logger=logger)

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 3
    assert "Managed candidate cap exceeded: discovered 2 managed tasks (limit: 1)." in capsys.readouterr().err


def test_main_apply_rerun_only_retries_redundant_update_after_partial_failure(monkeypatch, tmp_path):
    config_path = write_config(tmp_path, mode="apply")
    db_path = create_db(tmp_path)
    logger, handler = build_logger("tests.local_sync_integration.partial_failure_rerun")

    insert_task(
        db_path,
        uuid="base",
        task_type=TASK_TYPE,
        title="Essay",
        notes=managed_note("2026-04-12"),
        deadline=encode_deadline(date(2026, 4, 10)),
    )
    insert_task(
        db_path,
        uuid="update",
        task_type=TASK_TYPE,
        title="[UPDATE] Essay",
        notes=managed_note("2026-04-14"),
    )

    run_counter = {"count": 0}

    def execution_builder(script: str, mutations: Sequence[LocalSyncTaskMutation]) -> AppleScriptExecutionResult:
        run_counter["count"] += 1
        if run_counter["count"] == 1:
            assert [mutation.task_id for mutation in mutations] == ["base", "update"]
            payload = [
                mutation_payload(mutations[0]),
                mutation_payload(
                    mutations[1],
                    success=False,
                    trash_verified=False,
                    trash_attempts=VERIFY_ATTEMPTS,
                    error=f"Failed to verify trash move after {VERIFY_ATTEMPTS} attempts. (-2700)",
                ),
            ]
            return AppleScriptExecutionResult(returncode=0, stdout=json.dumps(payload), stderr="")

        assert [mutation.task_id for mutation in mutations] == ["update"]
        assert "base" not in script
        payload = [mutation_payload(mutations[0])]
        return AppleScriptExecutionResult(returncode=0, stdout=json.dumps(payload), stderr="")

    install_main_integration(
        monkeypatch,
        db_path=db_path,
        logger=logger,
        execution_builder=execution_builder,
    )

    first_exit_code = main(["--config", str(config_path)])

    assert first_exit_code == 4
    assert run_counter["count"] == 1
    assert any("mutation_results=2/2 successes=1 failures=1" in message for message in handler.messages)

    update_task_fields(db_path, uuid="base", deadline=encode_deadline(date(2026, 4, 14)))
    handler.messages.clear()

    second_exit_code = main(["--config", str(config_path)])

    assert second_exit_code == 0
    assert run_counter["count"] == 2
    assert any("Mutation result task_id=update" in message for message in handler.messages)
    assert not any("Mutation result task_id=base" in message for message in handler.messages)
    assert any(
        "Local sync summary exit_code=0 mode=apply scope=Inbox discovered=2 managed=2 writable=2 canonical=1 redundant_updates=1 diagnostics=0 planned_mutations=1 mutation_results=1/1 successes=1 failures=0"
        in message
        for message in handler.messages
    )


def test_main_returns_timeout_before_apply(monkeypatch, tmp_path, capsys):
    config_path = write_config(tmp_path, mode="apply")
    db_path = create_db(tmp_path)
    logger, handler = build_logger("tests.local_sync_integration.timeout_pre_apply")
    timeout_guard = FakeTimeoutGuard()
    timeout_guard.raise_pre_apply = True

    insert_task(
        db_path,
        uuid="task-1",
        task_type=TASK_TYPE,
        title="Essay",
        notes=managed_note("2026-04-10"),
    )

    apply_calls: list[Sequence[LocalSyncTaskMutation]] = []

    def execution_builder(script: str, mutations: Sequence[LocalSyncTaskMutation]) -> AppleScriptExecutionResult:
        apply_calls.append(mutations)
        raise AssertionError(f"apply should not run when the timeout triggers before apply: {script}")

    install_main_integration(
        monkeypatch,
        db_path=db_path,
        logger=logger,
        execution_builder=execution_builder,
        timeout_guard=timeout_guard,
    )

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 5
    assert timeout_guard.pre_apply_calls == 1
    assert apply_calls == []
    assert "Local sync timeout exceeded before apply after 10.0s (limit: 10.0s)." in capsys.readouterr().err
    assert any("planned_mutations=1" in message for message in handler.messages)


def test_main_returns_timeout_between_result_steps(monkeypatch, tmp_path, capsys):
    config_path = write_config(tmp_path, mode="apply")
    db_path = create_db(tmp_path)
    logger, handler = build_logger("tests.local_sync_integration.timeout_during_apply")
    timeout_guard = FakeTimeoutGuard(error_on_step="task-1")

    insert_task(
        db_path,
        uuid="task-1",
        task_type=TASK_TYPE,
        title="Essay",
        notes=managed_note("2026-04-10"),
    )
    insert_task(
        db_path,
        uuid="task-2",
        task_type=TASK_TYPE,
        title="Quiz",
        notes=managed_note("2026-04-11"),
    )

    def execution_builder(script: str, mutations: Sequence[LocalSyncTaskMutation]) -> AppleScriptExecutionResult:
        assert [mutation.task_id for mutation in mutations] == ["task-1", "task-2"]
        payload = [mutation_payload(mutation) for mutation in mutations]
        return AppleScriptExecutionResult(returncode=0, stdout=json.dumps(payload), stderr="")

    install_main_integration(
        monkeypatch,
        db_path=db_path,
        logger=logger,
        execution_builder=execution_builder,
        timeout_guard=timeout_guard,
    )

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 5
    assert timeout_guard.pre_apply_calls == 1
    assert timeout_guard.step_calls == ["task-1"]
    assert any("Mutation result task_id=task-1" in message for message in handler.messages)
    assert not any("Mutation result task_id=task-2" in message for message in handler.messages)
    assert any("mutation_results=1/2 successes=1 failures=0" in message for message in handler.messages)
    assert "Local sync timeout exceeded between task result steps (task-1) after 10.0s (limit: 10.0s)." in capsys.readouterr().err
