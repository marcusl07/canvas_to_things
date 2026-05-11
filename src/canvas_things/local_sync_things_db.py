"""Read-only Things SQLite discovery for the local sync companion."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

import sqlite3

THINGS_GROUP_CONTAINER_ID = "JLMPQHK86H.com.culturedcode.ThingsMac"
THINGS_GROUP_CONTAINER_PATH = Path.home() / "Library/Group Containers" / THINGS_GROUP_CONTAINER_ID
THINGS_DATABASE_GLOB = "ThingsData-*/Things Database.thingsdatabase/main.sqlite"

TASK_TYPE = 0
PROJECT_TYPE = 1
HEADING_TYPE = 2
OPEN_STATUS = 0

_REQUIRED_TASK_COLUMNS = frozenset(
    {
        "uuid",
        "type",
        "status",
        "trashed",
        "title",
        "notes",
        "deadline",
        "activation_date",
        "project",
        "heading",
    }
)


class LocalSyncThingsDBError(RuntimeError):
    """Base error for read-only Things database discovery."""


class LocalSyncThingsDBNotFoundError(LocalSyncThingsDBError):
    """Raised when the Things SQLite store cannot be found."""


class LocalSyncThingsDBSchemaError(LocalSyncThingsDBError):
    """Raised when the Things SQLite schema is not the expected shape."""


class LocalSyncThingsDBScopeError(LocalSyncThingsDBError):
    """Raised when the configured discovery scope is invalid or ambiguous."""


@dataclass(frozen=True)
class ThingsScope:
    """Resolved discovery scope for one read-only Things query."""

    kind: str
    project_uuid: str | None = None
    project_title: str | None = None


@dataclass(frozen=True)
class ThingsTaskRecord:
    """Open Things task row decoded for local deadline sync planning."""

    uuid: str
    title: str
    notes: str | None
    deadline_value: int | None
    deadline_date: date | None
    activation_date_value: int | None
    activation_date: date | None
    project_uuid: str | None
    project_title: str | None
    heading_uuid: str | None


@dataclass(frozen=True)
class ThingsDiscoveryResult:
    """All read-only discovery output for one scope resolution."""

    db_path: Path
    scope: ThingsScope
    tasks: tuple[ThingsTaskRecord, ...]


def resolve_things_db_path(
    db_path: Path | None = None,
    *,
    group_container_path: Path = THINGS_GROUP_CONTAINER_PATH,
) -> Path:
    """Resolve the active Things SQLite path from the default group container."""

    if db_path is not None:
        resolved_path = db_path.expanduser().resolve()
        if not resolved_path.exists():
            raise LocalSyncThingsDBNotFoundError(f"Things database not found at {resolved_path}.")
        if not resolved_path.is_file():
            raise LocalSyncThingsDBNotFoundError(f"Things database path is not a file: {resolved_path}.")
        return resolved_path

    matches = sorted(group_container_path.glob(THINGS_DATABASE_GLOB))
    if not matches:
        raise LocalSyncThingsDBNotFoundError(
            "Could not locate the Things database under "
            f"{group_container_path / 'ThingsData-*/Things Database.thingsdatabase/main.sqlite'}."
        )
    if len(matches) > 1:
        formatted_matches = ", ".join(str(path) for path in matches)
        raise LocalSyncThingsDBNotFoundError(
            "Found multiple Things databases. Pass an explicit path instead: "
            f"{formatted_matches}."
        )
    return matches[0].resolve()


def discover_open_tasks(
    project_title: str | None,
    *,
    move_to_project: str | None = None,
    db_path: Path | None = None,
    group_container_path: Path = THINGS_GROUP_CONTAINER_PATH,
) -> ThingsDiscoveryResult:
    """Discover open Things tasks for the configured local-sync scope."""

    resolved_db_path = resolve_things_db_path(db_path, group_container_path=group_container_path)
    with connect_readonly_things_db(resolved_db_path) as connection:
        _validate_schema(connection)
        scope = resolve_scope(connection, project_title)
        tasks = _load_discovery_tasks(
            connection,
            scope=scope,
            move_to_project=move_to_project,
        )
    return ThingsDiscoveryResult(db_path=resolved_db_path, scope=scope, tasks=tasks)


def resolve_scope(connection: sqlite3.Connection, project_title: str | None) -> ThingsScope:
    """Resolve Inbox or a unique open project title into a concrete scope."""

    if project_title is None:
        return ThingsScope(kind="inbox")

    rows = connection.execute(
        """
        SELECT uuid, title
        FROM TMTask
        WHERE type = ?
          AND status = ?
          AND trashed = 0
          AND title = ?
        ORDER BY uuid
        """,
        (PROJECT_TYPE, OPEN_STATUS, project_title),
    ).fetchall()

    if not rows:
        raise LocalSyncThingsDBScopeError(f"No open Things project named {project_title!r} was found.")
    if len(rows) > 1:
        raise LocalSyncThingsDBScopeError(
            f"Duplicate open Things projects named {project_title!r} make scope resolution ambiguous."
        )

    row = rows[0]
    return ThingsScope(kind="project", project_uuid=row["uuid"], project_title=row["title"])


def decode_things_deadline(value: int | None) -> date | None:
    """Decode Things' packed local date integer into a Python date."""

    if value in (None, 0):
        return None
    if not isinstance(value, int):
        raise LocalSyncThingsDBSchemaError(f"Unexpected Things deadline value {value!r}.")
    if value & 0x7F:
        raise LocalSyncThingsDBSchemaError(f"Unexpected Things deadline value {value!r}.")

    year = value >> 16
    month = (value >> 12) & 0x0F
    day = (value >> 7) & 0x1F
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise LocalSyncThingsDBSchemaError(f"Unexpected Things deadline value {value!r}.") from exc


@contextmanager
def connect_readonly_things_db(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open the Things database in read-only SQLite mode."""

    uri = f"file:{quote(str(db_path))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        yield connection
    finally:
        connection.close()


def _validate_schema(connection: sqlite3.Connection) -> None:
    task_columns = _read_table_columns(connection, "TMTask")
    missing_columns = sorted(_REQUIRED_TASK_COLUMNS.difference(task_columns))
    if missing_columns:
        raise LocalSyncThingsDBSchemaError(
            "Unexpected TMTask schema. Missing required columns: " + ", ".join(missing_columns) + "."
        )


def _read_table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    if not rows:
        raise LocalSyncThingsDBSchemaError(f"Expected SQLite table {table_name} was not found.")
    return {str(row["name"]) for row in rows}


def _load_open_tasks(
    connection: sqlite3.Connection,
    scope: ThingsScope,
) -> tuple[ThingsTaskRecord, ...]:
    if scope.kind == "inbox":
        rows = connection.execute(
            """
            WITH RECURSIVE inbox_headings(uuid) AS (
                SELECT uuid
                FROM TMTask
                WHERE type = :heading_type
                  AND status = :open_status
                  AND trashed = 0
                  AND COALESCE(project, '') = ''
                  AND COALESCE(heading, '') = ''
                UNION ALL
                SELECT child.uuid
                FROM TMTask AS child
                JOIN inbox_headings AS parent
                  ON child.heading = parent.uuid
                WHERE child.type = :heading_type
                  AND child.status = :open_status
                  AND child.trashed = 0
            )
            SELECT
                task.uuid,
                task.title,
                task.notes,
                task.deadline,
                task.activation_date,
                task.heading,
                NULL AS project_uuid,
                NULL AS project_title
            FROM TMTask AS task
            WHERE task.type = :task_type
              AND task.status = :open_status
              AND task.trashed = 0
              AND COALESCE(task.project, '') = ''
              AND (
                    COALESCE(task.heading, '') = ''
                    OR task.heading IN (SELECT uuid FROM inbox_headings)
                  )
            ORDER BY lower(task.title), task.uuid
            """,
            {
                "heading_type": HEADING_TYPE,
                "open_status": OPEN_STATUS,
                "task_type": TASK_TYPE,
            },
        ).fetchall()
    elif scope.kind == "project" and scope.project_uuid is not None and scope.project_title is not None:
        rows = connection.execute(
            """
            WITH RECURSIVE scope_headings(uuid) AS (
                SELECT uuid
                FROM TMTask
                WHERE type = :heading_type
                  AND status = :open_status
                  AND trashed = 0
                  AND project = :project_uuid
                UNION ALL
                SELECT child.uuid
                FROM TMTask AS child
                JOIN scope_headings AS parent
                  ON child.heading = parent.uuid
                WHERE child.type = :heading_type
                  AND child.status = :open_status
                  AND child.trashed = 0
            )
            SELECT
                task.uuid,
                task.title,
                task.notes,
                task.deadline,
                task.activation_date,
                task.heading,
                :project_uuid AS project_uuid,
                :project_title AS project_title
            FROM TMTask AS task
            WHERE task.type = :task_type
              AND task.status = :open_status
              AND task.trashed = 0
              AND (
                    task.project = :project_uuid
                    OR task.heading IN (SELECT uuid FROM scope_headings)
                  )
            ORDER BY lower(task.title), task.uuid
            """,
            {
                "heading_type": HEADING_TYPE,
                "open_status": OPEN_STATUS,
                "project_title": scope.project_title,
                "project_uuid": scope.project_uuid,
                "task_type": TASK_TYPE,
            },
        ).fetchall()
    else:
        raise LocalSyncThingsDBScopeError(f"Unsupported Things scope {scope!r}.")

    return tuple(_row_to_task(row) for row in rows)


def _load_discovery_tasks(
    connection: sqlite3.Connection,
    *,
    scope: ThingsScope,
    move_to_project: str | None,
) -> tuple[ThingsTaskRecord, ...]:
    tasks = _load_open_tasks(connection, scope)
    if scope.kind != "inbox" or move_to_project is None:
        return tasks

    destination_scope = resolve_scope(connection, move_to_project)
    destination_tasks = _load_open_tasks(connection, destination_scope)
    return _merge_discovery_tasks(tasks, destination_tasks)


def _merge_discovery_tasks(
    first_scope_tasks: tuple[ThingsTaskRecord, ...],
    second_scope_tasks: tuple[ThingsTaskRecord, ...],
) -> tuple[ThingsTaskRecord, ...]:
    combined_tasks: list[ThingsTaskRecord] = []
    seen_task_uuids: set[str] = set()

    for task in (*first_scope_tasks, *second_scope_tasks):
        if task.uuid in seen_task_uuids:
            raise LocalSyncThingsDBSchemaError(
                "Discovered the same Things task uuid in multiple local-sync discovery scopes: "
                f"{task.uuid!r}."
            )
        seen_task_uuids.add(task.uuid)
        combined_tasks.append(task)

    combined_tasks.sort(key=lambda task: (task.title.lower(), task.uuid))
    return tuple(combined_tasks)


def _row_to_task(row: sqlite3.Row) -> ThingsTaskRecord:
    deadline_value = row["deadline"]
    activation_date_value = row["activation_date"]
    return ThingsTaskRecord(
        uuid=row["uuid"],
        title=row["title"],
        notes=row["notes"],
        deadline_value=deadline_value,
        deadline_date=decode_things_deadline(deadline_value),
        activation_date_value=activation_date_value,
        activation_date=decode_things_deadline(activation_date_value),
        project_uuid=row["project_uuid"],
        project_title=row["project_title"],
        heading_uuid=row["heading"],
    )


__all__ = [
    "HEADING_TYPE",
    "LocalSyncThingsDBError",
    "LocalSyncThingsDBNotFoundError",
    "LocalSyncThingsDBSchemaError",
    "LocalSyncThingsDBScopeError",
    "OPEN_STATUS",
    "PROJECT_TYPE",
    "TASK_TYPE",
    "THINGS_DATABASE_GLOB",
    "THINGS_GROUP_CONTAINER_ID",
    "THINGS_GROUP_CONTAINER_PATH",
    "ThingsDiscoveryResult",
    "ThingsScope",
    "ThingsTaskRecord",
    "connect_readonly_things_db",
    "decode_things_deadline",
    "discover_open_tasks",
    "resolve_scope",
    "resolve_things_db_path",
]
