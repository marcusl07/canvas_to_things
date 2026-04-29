from __future__ import annotations

from datetime import date

import pytest

from canvas_things.local_sync_planner import (
    CLASSIFICATION_CANONICAL,
    CLASSIFICATION_DIAGNOSTIC_ONLY,
    CLASSIFICATION_REDUNDANT_UPDATE,
    LocalSyncCandidateCapError,
    build_local_sync_write_plan,
)
from canvas_things.local_sync_things_db import ThingsTaskRecord

PLAN_TODAY = date(2026, 4, 1)


def make_task(
    *,
    uuid: str,
    title: str,
    note_due: str | None,
    due_at_line: str | None = None,
    include_marker: bool = True,
    deadline_date: date | None = None,
    project_title: str | None = None,
) -> ThingsTaskRecord:
    lines = [title]
    if note_due is not None:
        lines.append(f"Due: {note_due}")
    if due_at_line is not None:
        lines.append(f"Due At: {due_at_line}")
    if include_marker:
        lines.append("Canvas:")
    notes = "\n".join(lines)
    return ThingsTaskRecord(
        uuid=uuid,
        title=title,
        notes=notes,
        deadline_value=None,
        deadline_date=deadline_date,
        project_uuid="project-1" if project_title is not None else None,
        project_title=project_title,
        heading_uuid=None,
    )


def entry_by_id(plan, task_id: str):
    return next(entry for entry in plan.entries if entry.candidate.task.uuid == task_id)


def test_build_local_sync_write_plan_rejects_managed_candidate_cap_exceeded():
    tasks = (
        make_task(uuid="task-1", title="Essay", note_due="2026-04-10"),
        make_task(uuid="task-2", title="Quiz", note_due=None),
        make_task(uuid="task-3", title="Ignored", note_due="2026-04-12", include_marker=False),
    )

    with pytest.raises(LocalSyncCandidateCapError, match="discovered 2 managed tasks"):
        build_local_sync_write_plan(tasks, candidate_cap=1, today=PLAN_TODAY)


def test_build_local_sync_write_plan_promotes_non_update_task_and_trashes_updates():
    tasks = (
        make_task(
            uuid="base",
            title="MATH201 - Problem Set 1",
            note_due="2026-04-10",
            deadline_date=date(2026, 4, 8),
            project_title="Inbox Mirror",
        ),
        make_task(
            uuid="update-1",
            title="[UPDATE] MATH201 - Problem Set 1",
            note_due="2026-04-12",
            deadline_date=date(2026, 4, 12),
        ),
        make_task(
            uuid="update-2",
            title="[UPDATE] MATH201 - Problem Set 1",
            note_due="2026-04-14",
        ),
    )

    plan = build_local_sync_write_plan(
        tasks,
        candidate_cap=200,
        move_to_project="School",
        today=PLAN_TODAY,
    )

    assert plan.managed_task_count == 3
    assert plan.writable_task_count == 3

    base_entry = entry_by_id(plan, "base")
    assert base_entry.classification == CLASSIFICATION_CANONICAL
    assert base_entry.planned_due_date == date(2026, 4, 14)
    assert base_entry.canonical_task_id == "base"
    assert base_entry.mutation is not None
    assert base_entry.mutation.update_due_date is True
    assert base_entry.mutation.due_date == date(2026, 4, 14)
    assert base_entry.mutation.project_target is not None
    assert base_entry.mutation.project_target.name == "School"

    update_entry = entry_by_id(plan, "update-1")
    assert update_entry.classification == CLASSIFICATION_REDUNDANT_UPDATE
    assert update_entry.canonical_task_id == "base"
    assert update_entry.mutation is not None
    assert update_entry.mutation.trash is True

    latest_update_entry = entry_by_id(plan, "update-2")
    assert latest_update_entry.classification == CLASSIFICATION_REDUNDANT_UPDATE
    assert latest_update_entry.canonical_task_id == "base"
    assert latest_update_entry.mutation is not None
    assert latest_update_entry.mutation.trash is True

    assert [mutation.task_id for mutation in plan.mutations] == ["base", "update-1", "update-2"]


def test_build_local_sync_write_plan_uses_note_due_dates_instead_of_current_things_deadlines():
    tasks = (
        make_task(
            uuid="base",
            title="History Essay",
            note_due="2026-04-10",
            deadline_date=date(2026, 4, 20),
        ),
        make_task(
            uuid="update",
            title="[UPDATE] History Essay",
            note_due="2026-04-15",
            deadline_date=date(2026, 4, 11),
        ),
    )

    plan = build_local_sync_write_plan(tasks, candidate_cap=200, today=PLAN_TODAY)
    base_entry = entry_by_id(plan, "base")

    assert base_entry.classification == CLASSIFICATION_CANONICAL
    assert base_entry.planned_due_date == date(2026, 4, 15)
    assert base_entry.mutation is not None
    assert base_entry.mutation.update_due_date is True
    assert base_entry.mutation.due_date == date(2026, 4, 15)

    update_entry = entry_by_id(plan, "update")
    assert update_entry.classification == CLASSIFICATION_REDUNDANT_UPDATE
    assert update_entry.mutation is not None
    assert update_entry.mutation.trash is True


def test_build_local_sync_write_plan_ignores_past_due_updates():
    tasks = (
        make_task(
            uuid="base",
            title="History Essay",
            note_due="2026-04-15",
            deadline_date=date(2026, 4, 15),
        ),
        make_task(
            uuid="past-update",
            title="[UPDATE] History Essay",
            note_due="2026-04-10",
            deadline_date=date(2026, 4, 10),
        ),
    )

    plan = build_local_sync_write_plan(tasks, candidate_cap=200, today=date(2026, 4, 12))

    assert plan.managed_task_count == 1
    assert [entry.candidate.task.uuid for entry in plan.entries] == ["base"]
    assert plan.mutations == ()


def test_build_local_sync_write_plan_keeps_latest_update_when_no_base_task_exists():
    tasks = (
        make_task(uuid="update-1", title="[UPDATE] Chem Lab", note_due="2026-04-11"),
        make_task(
            uuid="update-2",
            title="[UPDATE] Chem Lab",
            note_due="2026-04-14",
            deadline_date=date(2026, 4, 14),
        ),
    )

    plan = build_local_sync_write_plan(tasks, candidate_cap=200, today=PLAN_TODAY)

    first_update_entry = entry_by_id(plan, "update-1")
    assert first_update_entry.classification == CLASSIFICATION_CANONICAL
    assert first_update_entry.planned_due_date == date(2026, 4, 14)
    assert first_update_entry.mutation is not None
    assert first_update_entry.mutation.update_due_date is True
    assert first_update_entry.mutation.due_date == date(2026, 4, 14)

    second_update_entry = entry_by_id(plan, "update-2")
    assert second_update_entry.classification == CLASSIFICATION_REDUNDANT_UPDATE
    assert second_update_entry.canonical_task_id == "update-1"
    assert second_update_entry.mutation is not None
    assert second_update_entry.mutation.trash is True


def test_build_local_sync_write_plan_does_not_trash_non_update_duplicates():
    tasks = (
        make_task(uuid="base-1", title="Reading Response", note_due="2026-04-10"),
        make_task(uuid="base-2", title="Reading Response", note_due="2026-04-11"),
        make_task(uuid="update", title="[UPDATE] Reading Response", note_due="2026-04-12"),
    )

    plan = build_local_sync_write_plan(tasks, candidate_cap=200, today=PLAN_TODAY)

    first_base_entry = entry_by_id(plan, "base-1")
    assert first_base_entry.classification == CLASSIFICATION_CANONICAL
    assert first_base_entry.planned_due_date == date(2026, 4, 12)
    assert first_base_entry.mutation is not None
    assert first_base_entry.mutation.due_date == date(2026, 4, 12)

    second_base_entry = entry_by_id(plan, "base-2")
    assert second_base_entry.classification == CLASSIFICATION_CANONICAL
    assert second_base_entry.planned_due_date == date(2026, 4, 11)
    assert second_base_entry.mutation is not None
    assert second_base_entry.mutation.due_date == date(2026, 4, 11)

    update_entry = entry_by_id(plan, "update")
    assert update_entry.classification == CLASSIFICATION_REDUNDANT_UPDATE
    assert update_entry.canonical_task_id == "base-1"
    assert update_entry.mutation is not None
    assert update_entry.mutation.trash is True


def test_build_local_sync_write_plan_prefers_destination_project_non_update_as_primary_canonical():
    tasks = (
        make_task(uuid="update", title="[UPDATE] Reading Response", note_due="2026-04-14"),
        make_task(
            uuid="inbox-base",
            title="Reading Response",
            note_due="2026-04-12",
            deadline_date=date(2026, 4, 9),
        ),
        make_task(
            uuid="project-base",
            title="Reading Response",
            note_due="2026-04-10",
            deadline_date=date(2026, 4, 8),
            project_title="School",
        ),
    )

    plan = build_local_sync_write_plan(
        tasks,
        candidate_cap=200,
        move_to_project="School",
        today=PLAN_TODAY,
    )

    project_entry = entry_by_id(plan, "project-base")
    assert project_entry.classification == CLASSIFICATION_CANONICAL
    assert project_entry.planned_due_date == date(2026, 4, 14)
    assert project_entry.canonical_task_id == "project-base"
    assert project_entry.mutation is not None
    assert project_entry.mutation.due_date == date(2026, 4, 14)

    inbox_entry = entry_by_id(plan, "inbox-base")
    assert inbox_entry.classification == CLASSIFICATION_CANONICAL
    assert inbox_entry.planned_due_date == date(2026, 4, 12)
    assert inbox_entry.canonical_task_id == "inbox-base"
    assert inbox_entry.mutation is not None
    assert inbox_entry.mutation.due_date == date(2026, 4, 12)
    assert inbox_entry.mutation.project_target is not None
    assert inbox_entry.mutation.project_target.name == "School"

    update_entry = entry_by_id(plan, "update")
    assert update_entry.classification == CLASSIFICATION_REDUNDANT_UPDATE
    assert update_entry.canonical_task_id == "project-base"
    assert update_entry.mutation is not None
    assert update_entry.mutation.trash is True


def test_build_local_sync_write_plan_prefers_destination_project_update_when_only_updates_exist():
    tasks = (
        make_task(uuid="update-inbox", title="[UPDATE] Chem Lab", note_due="2026-04-14"),
        make_task(
            uuid="update-project",
            title="[UPDATE] Chem Lab",
            note_due="2026-04-10",
            deadline_date=date(2026, 4, 8),
            project_title="School",
        ),
    )

    plan = build_local_sync_write_plan(
        tasks,
        candidate_cap=200,
        move_to_project="School",
        today=PLAN_TODAY,
    )

    project_entry = entry_by_id(plan, "update-project")
    assert project_entry.classification == CLASSIFICATION_CANONICAL
    assert project_entry.planned_due_date == date(2026, 4, 14)
    assert project_entry.canonical_task_id == "update-project"
    assert project_entry.mutation is not None
    assert project_entry.mutation.update_due_date is True
    assert project_entry.mutation.due_date == date(2026, 4, 14)

    inbox_entry = entry_by_id(plan, "update-inbox")
    assert inbox_entry.classification == CLASSIFICATION_REDUNDANT_UPDATE
    assert inbox_entry.canonical_task_id == "update-project"
    assert inbox_entry.mutation is not None
    assert inbox_entry.mutation.trash is True


def test_build_local_sync_write_plan_orders_canonical_mutations_before_redundant_updates():
    tasks = (
        make_task(uuid="update", title="[UPDATE] Essay", note_due="2026-04-14"),
        make_task(
            uuid="base",
            title="Essay",
            note_due="2026-04-10",
            deadline_date=date(2026, 4, 8),
        ),
    )

    plan = build_local_sync_write_plan(tasks, candidate_cap=200, today=PLAN_TODAY)

    assert [entry.candidate.task.uuid for entry in plan.entries] == ["update", "base"]
    assert [mutation.task_id for mutation in plan.mutations] == ["base", "update"]


def test_build_local_sync_write_plan_keeps_malformed_due_tasks_as_diagnostics_only():
    tasks = (
        make_task(uuid="task-1", title="Physics Quiz", note_due="tomorrow"),
        make_task(uuid="task-2", title="No Marker", note_due="2026-04-10", include_marker=False),
    )

    plan = build_local_sync_write_plan(
        tasks,
        candidate_cap=200,
        move_to_project="School",
        today=PLAN_TODAY,
    )

    assert plan.managed_task_count == 1
    assert plan.writable_task_count == 0
    assert plan.mutations == ()

    diagnostic_entry = entry_by_id(plan, "task-1")
    assert diagnostic_entry.classification == CLASSIFICATION_DIAGNOSTIC_ONLY
    assert diagnostic_entry.planned_due_date is None
    assert diagnostic_entry.canonical_task_id is None
    assert diagnostic_entry.mutation is None
    assert [diagnostic.code for diagnostic in diagnostic_entry.candidate.parsed_note.diagnostics] == ["malformed_due"]


def test_build_local_sync_write_plan_shifts_weird_due_time_and_prefixes_title():
    tasks = (
        make_task(
            uuid="task-1",
            title="INF 141: Lab",
            note_due="2026-04-20",
            due_at_line="2026-04-21 00:00:00 UTC (2026-04-20 17:00:00 PDT)",
            deadline_date=date(2026, 4, 20),
        ),
    )

    plan = build_local_sync_write_plan(tasks, candidate_cap=200, today=PLAN_TODAY)
    entry = entry_by_id(plan, "task-1")

    assert entry.planned_due_date == date(2026, 4, 19)
    assert entry.mutation is not None
    assert entry.mutation.update_due_date is True
    assert entry.mutation.due_date == date(2026, 4, 19)
    assert entry.mutation.update_title is True
    assert entry.mutation.new_title == "[DUE 1700] INF 141: Lab"


def test_build_local_sync_write_plan_removes_stale_weird_due_prefix_and_restores_deadline():
    tasks = (
        make_task(
            uuid="task-1",
            title="[DUE 1700] INF 141: Lab",
            note_due="2026-04-20",
            due_at_line="2026-04-21 06:59:59 UTC (2026-04-20 23:59:59 PDT)",
            deadline_date=date(2026, 4, 19),
        ),
    )

    plan = build_local_sync_write_plan(tasks, candidate_cap=200, today=PLAN_TODAY)
    entry = entry_by_id(plan, "task-1")

    assert entry.planned_due_date == date(2026, 4, 20)
    assert entry.mutation is not None
    assert entry.mutation.update_due_date is True
    assert entry.mutation.due_date == date(2026, 4, 20)
    assert entry.mutation.update_title is True
    assert entry.mutation.new_title == "INF 141: Lab"


def test_build_local_sync_write_plan_groups_titles_with_due_prefix_after_update_prefix():
    tasks = (
        make_task(
            uuid="base",
            title="[DUE 1700] INF 141: Lab",
            note_due="2026-04-20",
            due_at_line="2026-04-21 00:00:00 UTC (2026-04-20 17:00:00 PDT)",
            deadline_date=date(2026, 4, 19),
        ),
        make_task(
            uuid="update",
            title="[UPDATE] [DUE 1700] INF 141: Lab",
            note_due="2026-04-20",
            due_at_line="2026-04-21 00:00:00 UTC (2026-04-20 17:00:00 PDT)",
            deadline_date=date(2026, 4, 19),
        ),
    )

    plan = build_local_sync_write_plan(tasks, candidate_cap=200, today=PLAN_TODAY)

    assert entry_by_id(plan, "base").classification == CLASSIFICATION_CANONICAL
    assert entry_by_id(plan, "update").classification == CLASSIFICATION_REDUNDANT_UPDATE
    assert entry_by_id(plan, "update").canonical_task_id == "base"
