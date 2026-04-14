from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from canvas_things.local_sync_things_db import (
    LocalSyncThingsDBNotFoundError,
    LocalSyncThingsDBSchemaError,
    LocalSyncThingsDBScopeError,
    decode_things_deadline,
    discover_open_tasks,
    resolve_things_db_path,
)


def create_db(tmp_path: Path, *, missing_columns: set[str] | None = None) -> Path:
    missing_columns = missing_columns or set()
    db_path = tmp_path / "main.sqlite"
    columns = [
        ("uuid", "TEXT PRIMARY KEY"),
        ("type", "INTEGER"),
        ("status", "INTEGER"),
        ("trashed", "INTEGER"),
        ("title", "TEXT"),
        ("notes", "TEXT"),
        ("deadline", "INTEGER"),
        ("project", "TEXT"),
        ("heading", "TEXT"),
    ]
    active_columns = [definition for definition in columns if definition[0] not in missing_columns]
    column_sql = ", ".join(f"{name} {type_sql}" for name, type_sql in active_columns)

    connection = sqlite3.connect(db_path)
    try:
        connection.execute(f"CREATE TABLE TMTask ({column_sql})")
        connection.commit()
    finally:
        connection.close()
    return db_path


def insert_task(
    db_path: Path,
    *,
    uuid: str,
    type: int,
    title: str,
    status: int = 0,
    trashed: int = 0,
    notes: str | None = None,
    deadline: int | None = None,
    project: str | None = None,
    heading: str | None = None,
) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO TMTask (uuid, type, status, trashed, title, notes, deadline, project, heading)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (uuid, type, status, trashed, title, notes, deadline, project, heading),
        )
        connection.commit()
    finally:
        connection.close()


def encode_deadline(deadline_date: date) -> int:
    return (deadline_date.year << 16) | (deadline_date.month << 12) | (deadline_date.day << 7)


def test_resolve_things_db_path_discovers_single_candidate(tmp_path):
    db_path = (
        tmp_path
        / "ThingsData-ABCD"
        / "Things Database.thingsdatabase"
        / "main.sqlite"
    )
    db_path.parent.mkdir(parents=True)
    db_path.write_text("", encoding="utf-8")

    assert resolve_things_db_path(group_container_path=tmp_path) == db_path.resolve()


def test_resolve_things_db_path_rejects_multiple_candidates(tmp_path):
    first_path = tmp_path / "ThingsData-AAAA" / "Things Database.thingsdatabase" / "main.sqlite"
    second_path = tmp_path / "ThingsData-BBBB" / "Things Database.thingsdatabase" / "main.sqlite"
    first_path.parent.mkdir(parents=True)
    second_path.parent.mkdir(parents=True)
    first_path.write_text("", encoding="utf-8")
    second_path.write_text("", encoding="utf-8")

    with pytest.raises(LocalSyncThingsDBNotFoundError, match="multiple Things databases"):
        resolve_things_db_path(group_container_path=tmp_path)


def test_discover_open_tasks_inbox_scope_only_returns_open_inbox_tasks(tmp_path):
    db_path = create_db(tmp_path)
    insert_task(db_path, uuid="orphan-heading", type=2, title="Loose Heading")
    insert_task(
        db_path,
        uuid="inbox-task",
        type=0,
        title="Inbox Task",
        notes="Canvas:\nDue: 2026-04-10",
        deadline=encode_deadline(date(2026, 4, 10)),
    )
    insert_task(db_path, uuid="headed-inbox-task", type=0, title="Headed Inbox", heading="orphan-heading")
    insert_task(db_path, uuid="project-row", type=1, title="Assignments")
    insert_task(db_path, uuid="project-task", type=0, title="Project Task", project="project-row")
    insert_task(db_path, uuid="completed-task", type=0, title="Completed", status=3)
    insert_task(db_path, uuid="trashed-task", type=0, title="Trashed", trashed=1)

    result = discover_open_tasks(None, db_path=db_path)

    assert result.scope.kind == "inbox"
    assert {task.title for task in result.tasks} == {"Headed Inbox", "Inbox Task"}

    inbox_task = next(task for task in result.tasks if task.uuid == "inbox-task")
    assert inbox_task.deadline_value == encode_deadline(date(2026, 4, 10))
    assert inbox_task.deadline_date == date(2026, 4, 10)
    assert inbox_task.project_uuid is None
    assert inbox_task.project_title is None


def test_discover_open_tasks_named_project_includes_heading_descendants(tmp_path):
    db_path = create_db(tmp_path)
    insert_task(db_path, uuid="project-row", type=1, title="Assignments")
    insert_task(db_path, uuid="other-project", type=1, title="Other")
    insert_task(db_path, uuid="heading-top", type=2, title="Waiting", project="project-row")
    insert_task(db_path, uuid="heading-child", type=2, title="Nested", heading="heading-top")
    insert_task(db_path, uuid="direct-task", type=0, title="Direct", project="project-row")
    insert_task(db_path, uuid="heading-task", type=0, title="Under Heading", heading="heading-top")
    insert_task(db_path, uuid="nested-heading-task", type=0, title="Nested Task", heading="heading-child")
    insert_task(db_path, uuid="other-task", type=0, title="Other Project Task", project="other-project")
    insert_task(db_path, uuid="inbox-task", type=0, title="Inbox Task")
    insert_task(db_path, uuid="completed-task", type=0, title="Completed Direct", project="project-row", status=3)

    result = discover_open_tasks("Assignments", db_path=db_path)

    assert result.scope.kind == "project"
    assert result.scope.project_uuid == "project-row"
    assert result.scope.project_title == "Assignments"
    assert {task.title for task in result.tasks} == {"Direct", "Nested Task", "Under Heading"}
    assert {task.project_uuid for task in result.tasks} == {"project-row"}
    assert {task.project_title for task in result.tasks} == {"Assignments"}


def test_discover_open_tasks_inbox_scope_with_move_to_project_merges_exactly_two_scopes(tmp_path):
    db_path = create_db(tmp_path)
    insert_task(db_path, uuid="project-row", type=1, title="Assignments")
    insert_task(db_path, uuid="other-project", type=1, title="Other")
    insert_task(db_path, uuid="inbox-heading", type=2, title="Inbox Heading")
    insert_task(db_path, uuid="project-heading", type=2, title="Project Heading", project="project-row")
    insert_task(db_path, uuid="b-inbox-essay", type=0, title="Essay")
    insert_task(db_path, uuid="d-inbox-zoo", type=0, title="Zoo Inbox", heading="inbox-heading")
    insert_task(db_path, uuid="a-project-essay", type=0, title="essay", project="project-row")
    insert_task(db_path, uuid="c-project-beta", type=0, title="Beta Project", heading="project-heading")
    insert_task(db_path, uuid="other-task", type=0, title="Other Project Task", project="other-project")

    result = discover_open_tasks(None, move_to_project="Assignments", db_path=db_path)

    assert result.scope.kind == "inbox"
    assert [task.uuid for task in result.tasks] == [
        "c-project-beta",
        "a-project-essay",
        "b-inbox-essay",
        "d-inbox-zoo",
    ]

    project_task = next(task for task in result.tasks if task.uuid == "a-project-essay")
    assert project_task.project_uuid == "project-row"
    assert project_task.project_title == "Assignments"

    inbox_task = next(task for task in result.tasks if task.uuid == "b-inbox-essay")
    assert inbox_task.project_uuid is None
    assert inbox_task.project_title is None


def test_discover_open_tasks_rejects_duplicate_exact_title_projects(tmp_path):
    db_path = create_db(tmp_path)
    insert_task(db_path, uuid="project-1", type=1, title="Assignments")
    insert_task(db_path, uuid="project-2", type=1, title="Assignments")

    with pytest.raises(LocalSyncThingsDBScopeError, match="Duplicate open Things projects named 'Assignments'"):
        discover_open_tasks("Assignments", db_path=db_path)


def test_discover_open_tasks_rejects_unknown_project_scope(tmp_path):
    db_path = create_db(tmp_path)

    with pytest.raises(LocalSyncThingsDBScopeError, match="No open Things project named 'Assignments'"):
        discover_open_tasks("Assignments", db_path=db_path)


def test_discover_open_tasks_rejects_unknown_move_to_project_scope(tmp_path):
    db_path = create_db(tmp_path)

    with pytest.raises(LocalSyncThingsDBScopeError, match="No open Things project named 'Assignments'"):
        discover_open_tasks(None, move_to_project="Assignments", db_path=db_path)


def test_discover_open_tasks_rejects_ambiguous_move_to_project_scope(tmp_path):
    db_path = create_db(tmp_path)
    insert_task(db_path, uuid="project-1", type=1, title="Assignments")
    insert_task(db_path, uuid="project-2", type=1, title="Assignments")

    with pytest.raises(LocalSyncThingsDBScopeError, match="Duplicate open Things projects named 'Assignments'"):
        discover_open_tasks(None, move_to_project="Assignments", db_path=db_path)


def test_discover_open_tasks_rejects_duplicate_uuid_across_dual_scopes(tmp_path):
    db_path = create_db(tmp_path)
    insert_task(db_path, uuid="project-row", type=1, title="Assignments")
    insert_task(db_path, uuid="inbox-heading", type=2, title="Inbox Heading")
    insert_task(
        db_path,
        uuid="project-heading",
        type=2,
        title="Project Heading",
        project="project-row",
        heading="inbox-heading",
    )
    insert_task(db_path, uuid="shared-task", type=0, title="Shared Task", heading="project-heading")

    with pytest.raises(LocalSyncThingsDBSchemaError, match="same Things task uuid"):
        discover_open_tasks(None, move_to_project="Assignments", db_path=db_path)


def test_discover_open_tasks_rejects_schema_drift(tmp_path):
    db_path = create_db(tmp_path, missing_columns={"heading"})

    with pytest.raises(LocalSyncThingsDBSchemaError, match="Missing required columns: heading"):
        discover_open_tasks(None, db_path=db_path)


def test_decode_things_deadline_rejects_unexpected_bit_pattern():
    with pytest.raises(LocalSyncThingsDBSchemaError, match="Unexpected Things deadline value"):
        decode_things_deadline(123)
