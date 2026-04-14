"""Managed-candidate classification and write-plan building for local sync."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence

from .local_sync_applescript import LocalSyncProjectTarget, LocalSyncTaskMutation
from .local_sync_notes import ParsedTaskNote, parse_task_note
from .local_sync_things_db import ThingsTaskRecord

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
class LocalSyncPlanEntry:
    """One managed task classified for diagnostics or mutation planning."""

    candidate: LocalSyncManagedTask
    classification: str
    planned_due_date: date | None
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
) -> LocalSyncWritePlan:
    """Build the managed-task write plan for one discovery result."""

    managed_candidates = tuple(
        candidate
        for candidate in (_build_managed_candidate(task) for task in tasks)
        if candidate.parsed_note.managed
    )
    if len(managed_candidates) > candidate_cap:
        raise LocalSyncCandidateCapError(
            f"Managed candidate cap exceeded: discovered {len(managed_candidates)} managed tasks "
            f"(limit: {candidate_cap})."
        )

    writable_candidates = [candidate for candidate in managed_candidates if candidate.parsed_note.writable]
    canonical_ids_to_due_dates, redundant_ids_to_canonical_ids = _resolve_group_plans(
        writable_candidates,
        destination_project_title=move_to_project,
    )

    entries_by_task_id: dict[str, LocalSyncPlanEntry] = {}
    for candidate in managed_candidates:
        entries_by_task_id[candidate.task.uuid] = _build_plan_entry(
            candidate,
            canonical_ids_to_due_dates=canonical_ids_to_due_dates,
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


def _resolve_group_plans(
    writable_candidates: Sequence[LocalSyncManagedTask],
    *,
    destination_project_title: str | None,
) -> tuple[dict[str, date], dict[str, str]]:
    grouped_candidates: dict[str, list[LocalSyncManagedTask]] = {}
    for candidate in writable_candidates:
        grouped_candidates.setdefault(candidate.normalized_title, []).append(candidate)

    canonical_due_dates: dict[str, date] = {}
    redundant_ids_to_canonical_ids: dict[str, str] = {}
    for group in grouped_candidates.values():
        latest_due_date = max(candidate.parsed_note.due_date for candidate in group if candidate.parsed_note.due_date is not None)
        primary_candidate = _choose_primary_canonical_candidate(
            group,
            destination_project_title=destination_project_title,
        )

        non_update_candidates = [candidate for candidate in group if not candidate.is_update_notification]
        if non_update_candidates:
            canonical_due_dates[primary_candidate.task.uuid] = latest_due_date
            for candidate in non_update_candidates:
                if candidate.task.uuid == primary_candidate.task.uuid:
                    continue
                canonical_due_dates[candidate.task.uuid] = candidate.parsed_note.due_date
            for candidate in group:
                if candidate.is_update_notification:
                    redundant_ids_to_canonical_ids[candidate.task.uuid] = primary_candidate.task.uuid
            continue

        canonical_due_dates[primary_candidate.task.uuid] = latest_due_date
        for candidate in group:
            if candidate.task.uuid != primary_candidate.task.uuid:
                redundant_ids_to_canonical_ids[candidate.task.uuid] = primary_candidate.task.uuid

    return canonical_due_dates, redundant_ids_to_canonical_ids


def _build_plan_entry(
    candidate: LocalSyncManagedTask,
    *,
    canonical_ids_to_due_dates: dict[str, date],
    redundant_ids_to_canonical_ids: dict[str, str],
    move_to_project: str | None,
) -> LocalSyncPlanEntry:
    canonical_due_date = canonical_ids_to_due_dates.get(candidate.task.uuid)
    if not candidate.parsed_note.writable:
        return LocalSyncPlanEntry(
            candidate=candidate,
            classification=CLASSIFICATION_DIAGNOSTIC_ONLY,
            planned_due_date=None,
            canonical_task_id=None,
            mutation=None,
        )

    if canonical_due_date is not None:
        mutation = _build_canonical_mutation(
            candidate,
            planned_due_date=canonical_due_date,
            move_to_project=move_to_project,
        )
        return LocalSyncPlanEntry(
            candidate=candidate,
            classification=CLASSIFICATION_CANONICAL,
            planned_due_date=canonical_due_date,
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
    planned_due_date: date,
    move_to_project: str | None,
) -> LocalSyncTaskMutation | None:
    update_due_date = candidate.task.deadline_date != planned_due_date
    project_target = None
    if move_to_project is not None and candidate.task.project_title != move_to_project:
        project_target = LocalSyncProjectTarget(name=move_to_project)

    if not update_due_date and project_target is None:
        return None

    return LocalSyncTaskMutation(
        task_id=candidate.task.uuid,
        title=candidate.task.title,
        update_due_date=update_due_date,
        due_date=planned_due_date if update_due_date else None,
        project_target=project_target,
    )


def is_update_notification_title(title: str) -> bool:
    """Return True when a Things task title came from an update email subject."""

    return title.startswith(UPDATE_TITLE_PREFIX)


def normalize_managed_title(title: str) -> str:
    """Normalize an update task title to the canonical group title."""

    if is_update_notification_title(title):
        return title[len(UPDATE_TITLE_PREFIX) :]
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
