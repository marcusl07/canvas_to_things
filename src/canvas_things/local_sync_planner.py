"""Managed-candidate classification and write-plan building for local sync."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence

from .local_sync_applescript import LocalSyncProjectTarget, LocalSyncTaskMutation
from .local_sync_notes import ParsedTaskNote, parse_task_note
from .local_sync_things_db import ThingsTaskRecord
from .managed_notes import format_weird_due_title_prefix, strip_weird_due_title_prefix

UPDATE_TITLE_PREFIX = "[UPDATE] "

CLASSIFICATION_CANONICAL = "canonical"
CLASSIFICATION_DIAGNOSTIC_ONLY = "diagnostic_only"
CLASSIFICATION_REDUNDANT_UPDATE = "redundant_update"


class LocalSyncPlannerError(RuntimeError):
    """Base error for local-sync planning failures."""


class LocalSyncCandidateCapError(LocalSyncPlannerError):
    """Raised when the managed candidate cap is exceeded."""


@dataclass(frozen=True)
class LocalSyncManagedTask:
    """A discovered task plus parsed managed-note metadata."""

    task: ThingsTaskRecord
    parsed_note: ParsedTaskNote
    normalized_title: str
    is_update_notification: bool


@dataclass(frozen=True)
class _PlannedDates:
    due_date: date
    schedule_date: date | None
    weird_due_display_time: str | None


@dataclass(frozen=True)
class LocalSyncPlanEntry:
    """One managed task classified for diagnostics or mutation planning."""

    candidate: LocalSyncManagedTask
    classification: str
    planned_due_date: date | None
    planned_schedule_date: date | None
    canonical_task_id: str | None
    mutation: LocalSyncTaskMutation | None


@dataclass(frozen=True)
class LocalSyncWritePlan:
    """Planner output for one local-sync discovery batch."""

    managed_candidates: tuple[LocalSyncManagedTask, ...]
    entries: tuple[LocalSyncPlanEntry, ...]
    mutations: tuple[LocalSyncTaskMutation, ...]

    @property
    def managed_task_count(self) -> int:
        return len(self.managed_candidates)

    @property
    def writable_task_count(self) -> int:
        return sum(1 for candidate in self.managed_candidates if candidate.parsed_note.writable)


def build_local_sync_write_plan(
    tasks: Sequence[ThingsTaskRecord] | Iterable[ThingsTaskRecord],
    *,
    candidate_cap: int,
    move_to_project: str | None = None,
    today: date | None = None,
) -> LocalSyncWritePlan:
    """Build the managed-task write plan for one discovery result."""

    if today is None:
        today = date.today()

    managed_candidates = tuple(
        candidate
        for candidate in (_build_managed_candidate(task) for task in tasks)
        if candidate.parsed_note.managed and not _is_past_due_candidate(candidate, today=today)
    )
    if len(managed_candidates) > candidate_cap:
        raise LocalSyncCandidateCapError(
            f"Managed candidate cap exceeded: discovered {len(managed_candidates)} managed tasks "
            f"(limit: {candidate_cap})."
        )

    writable_candidates = [candidate for candidate in managed_candidates if candidate.parsed_note.writable]
    canonical_ids_to_planned_dates, redundant_ids_to_canonical_ids = _resolve_group_plans(
        writable_candidates,
        destination_project_title=move_to_project,
    )

    entries_by_task_id: dict[str, LocalSyncPlanEntry] = {}
    for candidate in managed_candidates:
        entries_by_task_id[candidate.task.uuid] = _build_plan_entry(
            candidate,
            canonical_ids_to_planned_dates=canonical_ids_to_planned_dates,
            redundant_ids_to_canonical_ids=redundant_ids_to_canonical_ids,
            move_to_project=move_to_project,
        )

    entries = tuple(entries_by_task_id[candidate.task.uuid] for candidate in managed_candidates)
    canonical_mutations = tuple(
        entry.mutation
        for entry in entries
        if entry.classification == CLASSIFICATION_CANONICAL and entry.mutation is not None
    )
    redundant_update_mutations = tuple(
        entry.mutation
        for entry in entries
        if entry.classification == CLASSIFICATION_REDUNDANT_UPDATE and entry.mutation is not None
    )
    return LocalSyncWritePlan(
        managed_candidates=managed_candidates,
        entries=entries,
        mutations=canonical_mutations + redundant_update_mutations,
    )


def _build_managed_candidate(task: ThingsTaskRecord) -> LocalSyncManagedTask:
    parsed_note = parse_task_note(task.notes)
    return LocalSyncManagedTask(
        task=task,
        parsed_note=parsed_note,
        normalized_title=normalize_managed_title(task.title),
        is_update_notification=is_update_notification_title(task.title),
    )


def _is_past_due_candidate(candidate: LocalSyncManagedTask, *, today: date) -> bool:
    return candidate.parsed_note.due_date is not None and candidate.parsed_note.due_date < today


def _resolve_group_plans(
    writable_candidates: Sequence[LocalSyncManagedTask],
    *,
    destination_project_title: str | None,
) -> tuple[dict[str, _PlannedDates], dict[str, str]]:
    grouped_candidates: dict[str, list[LocalSyncManagedTask]] = {}
    for candidate in writable_candidates:
        grouped_candidates.setdefault(candidate.normalized_title, []).append(candidate)

    canonical_planned_dates: dict[str, _PlannedDates] = {}
    redundant_ids_to_canonical_ids: dict[str, str] = {}
    for group in grouped_candidates.values():
        latest_candidate = max(
            group,
            key=lambda candidate: candidate.parsed_note.effective_deadline_date or date.min,
        )
        latest_planned_dates = _planned_dates_for_candidate(latest_candidate)
        primary_candidate = _choose_primary_canonical_candidate(
            group,
            destination_project_title=destination_project_title,
        )

        non_update_candidates = [candidate for candidate in group if not candidate.is_update_notification]
        if non_update_candidates:
            canonical_planned_dates[primary_candidate.task.uuid] = latest_planned_dates
            for candidate in non_update_candidates:
                if candidate.task.uuid == primary_candidate.task.uuid:
                    continue
                canonical_planned_dates[candidate.task.uuid] = _planned_dates_for_candidate(candidate)
            for candidate in group:
                if candidate.is_update_notification:
                    redundant_ids_to_canonical_ids[candidate.task.uuid] = primary_candidate.task.uuid
            continue

        canonical_planned_dates[primary_candidate.task.uuid] = latest_planned_dates
        for candidate in group:
            if candidate.task.uuid != primary_candidate.task.uuid:
                redundant_ids_to_canonical_ids[candidate.task.uuid] = primary_candidate.task.uuid

    return canonical_planned_dates, redundant_ids_to_canonical_ids


def _planned_dates_for_candidate(candidate: LocalSyncManagedTask) -> _PlannedDates:
    if candidate.parsed_note.effective_deadline_date is None:
        raise AssertionError("Writable managed candidates must have a planned deadline.")
    return _PlannedDates(
        due_date=candidate.parsed_note.effective_deadline_date,
        schedule_date=candidate.parsed_note.early_schedule_date,
        weird_due_display_time=candidate.parsed_note.weird_due_display_time,
    )


def _build_plan_entry(
    candidate: LocalSyncManagedTask,
    *,
    canonical_ids_to_planned_dates: dict[str, _PlannedDates],
    redundant_ids_to_canonical_ids: dict[str, str],
    move_to_project: str | None,
) -> LocalSyncPlanEntry:
    planned_dates = canonical_ids_to_planned_dates.get(candidate.task.uuid)
    if not candidate.parsed_note.writable:
        return LocalSyncPlanEntry(
            candidate=candidate,
            classification=CLASSIFICATION_DIAGNOSTIC_ONLY,
            planned_due_date=None,
            planned_schedule_date=None,
            canonical_task_id=None,
            mutation=None,
        )

    if planned_dates is not None:
        mutation = _build_canonical_mutation(
            candidate,
            planned_dates=planned_dates,
            move_to_project=move_to_project,
        )
        return LocalSyncPlanEntry(
            candidate=candidate,
            classification=CLASSIFICATION_CANONICAL,
            planned_due_date=planned_dates.due_date,
            planned_schedule_date=planned_dates.schedule_date,
            canonical_task_id=candidate.task.uuid,
            mutation=mutation,
        )

    mutation = LocalSyncTaskMutation(
        task_id=candidate.task.uuid,
        title=candidate.task.title,
        trash=True,
    )
    canonical_candidate = redundant_ids_to_canonical_ids.get(candidate.task.uuid)
    return LocalSyncPlanEntry(
        candidate=candidate,
        classification=CLASSIFICATION_REDUNDANT_UPDATE,
        planned_due_date=None,
        planned_schedule_date=None,
        canonical_task_id=canonical_candidate,
        mutation=mutation,
    )


def _choose_primary_canonical_candidate(
    group: Sequence[LocalSyncManagedTask],
    *,
    destination_project_title: str | None,
) -> LocalSyncManagedTask:
    if not group:
        raise AssertionError("Expected at least one writable managed candidate per group.")

    return min(
        group,
        key=lambda candidate: _canonical_priority(
            candidate,
            destination_project_title=destination_project_title,
        ),
    )


def _canonical_priority(
    candidate: LocalSyncManagedTask,
    *,
    destination_project_title: str | None,
) -> int:
    if destination_project_title is None:
        return 0 if not candidate.is_update_notification else 1

    if candidate.task.project_title == destination_project_title:
        return 0 if not candidate.is_update_notification else 2

    return 1 if not candidate.is_update_notification else 3


def _build_canonical_mutation(
    candidate: LocalSyncManagedTask,
    *,
    planned_dates: _PlannedDates,
    move_to_project: str | None,
) -> LocalSyncTaskMutation | None:
    update_due_date = candidate.task.deadline_date != planned_dates.due_date
    update_schedule_date = candidate.task.activation_date != planned_dates.schedule_date
    desired_title = _desired_task_title(candidate, planned_dates=planned_dates)
    update_title = candidate.task.title != desired_title
    project_target = None
    if move_to_project is not None and candidate.task.project_title != move_to_project:
        project_target = LocalSyncProjectTarget(name=move_to_project)

    if not update_due_date and not update_schedule_date and not update_title and project_target is None:
        return None

    return LocalSyncTaskMutation(
        task_id=candidate.task.uuid,
        title=candidate.task.title,
        update_due_date=update_due_date,
        due_date=planned_dates.due_date if update_due_date else None,
        update_schedule_date=update_schedule_date,
        schedule_date=planned_dates.schedule_date if update_schedule_date else None,
        update_title=update_title,
        new_title=desired_title if update_title else None,
        project_target=project_target,
    )


def is_update_notification_title(title: str) -> bool:
    """Return True when a Things task title came from an update email subject."""

    return title.startswith(UPDATE_TITLE_PREFIX)


def normalize_managed_title(title: str) -> str:
    """Normalize an update task title to the canonical group title."""

    if is_update_notification_title(title):
        return strip_weird_due_title_prefix(title[len(UPDATE_TITLE_PREFIX) :])
    return strip_weird_due_title_prefix(title)


def _desired_task_title(candidate: LocalSyncManagedTask, *, planned_dates: _PlannedDates) -> str:
    title = normalize_managed_title(candidate.task.title)
    if planned_dates.weird_due_display_time is not None:
        prefix = format_weird_due_title_prefix(
            planned_dates.weird_due_display_time,
            planned_dates.due_date,
        )
        title = f"{prefix}{title}"
    if candidate.is_update_notification:
        return f"{UPDATE_TITLE_PREFIX}{title}"
    return title


__all__ = [
    "CLASSIFICATION_CANONICAL",
    "CLASSIFICATION_DIAGNOSTIC_ONLY",
    "CLASSIFICATION_REDUNDANT_UPDATE",
    "LocalSyncCandidateCapError",
    "LocalSyncManagedTask",
    "LocalSyncPlanEntry",
    "LocalSyncPlannerError",
    "LocalSyncWritePlan",
    "UPDATE_TITLE_PREFIX",
    "build_local_sync_write_plan",
    "is_update_notification_title",
    "normalize_managed_title",
]
