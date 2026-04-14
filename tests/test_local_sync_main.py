from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from canvas_things.local_sync_applescript import LocalSyncTaskMutation, LocalSyncTaskMutationResult
from canvas_things.local_sync_main import main
from canvas_things.local_sync_notes import NoteDiagnostic, ParsedTaskNote
from canvas_things.local_sync_planner import (
    CLASSIFICATION_CANONICAL,
    CLASSIFICATION_DIAGNOSTIC_ONLY,
    LocalSyncManagedTask,
    LocalSyncPlanEntry,
    LocalSyncWritePlan,
)
from canvas_things.local_sync_runtime import LocalSyncTimeoutError
from canvas_things.local_sync_things_db import ThingsDiscoveryResult, ThingsScope, ThingsTaskRecord


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


def write_config(
    tmp_path: Path,
    *,
    mode: str = "dry-run",
    timeout_seconds: int = 30,
    move_to_project: str | None = None,
) -> Path:
    move_to_project_line = ""
    if move_to_project is not None:
        move_to_project_line = f'  move_to_project: "{move_to_project}"\n'

    path = tmp_path / "config.yml"
    path.write_text(
        f"""
version: 1
local_sync:
{move_to_project_line}  mode: "{mode}"
  timeout_seconds: {timeout_seconds}
""",
        encoding="utf-8",
    )
    return path


def build_logger(name: str) -> tuple[logging.Logger, ListHandler]:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = ListHandler()
    logger.addHandler(handler)
    return logger, handler


def build_task(uuid: str, title: str) -> ThingsTaskRecord:
    return ThingsTaskRecord(
        uuid=uuid,
        title=title,
        notes="Due: 2026-04-15\nCanvas:",
        deadline_value=None,
        deadline_date=None,
        project_uuid=None,
        project_title=None,
        heading_uuid=None,
    )


def build_entry(
    *,
    uuid: str,
    title: str,
    classification: str,
    mutation: LocalSyncTaskMutation | None,
    diagnostics: tuple[NoteDiagnostic, ...] = (),
) -> LocalSyncPlanEntry:
    candidate = LocalSyncManagedTask(
        task=build_task(uuid, title),
        parsed_note=ParsedTaskNote(
            managed=True,
            writable=mutation is not None or classification != CLASSIFICATION_DIAGNOSTIC_ONLY,
            due_date=date(2026, 4, 15) if classification != CLASSIFICATION_DIAGNOSTIC_ONLY else None,
            due_text="2026-04-15" if classification != CLASSIFICATION_DIAGNOSTIC_ONLY else None,
            marker_line_number=2,
            due_line_number=1 if classification != CLASSIFICATION_DIAGNOSTIC_ONLY else None,
            diagnostics=diagnostics,
        ),
        normalized_title=title,
        is_update_notification=False,
    )
    return LocalSyncPlanEntry(
        candidate=candidate,
        classification=classification,
        planned_due_date=date(2026, 4, 15) if mutation is not None else None,
        canonical_task_id=uuid if classification == CLASSIFICATION_CANONICAL else None,
        mutation=mutation,
    )


def build_discovery(task_count: int) -> ThingsDiscoveryResult:
    tasks = tuple(build_task(f"task-{index}", f"Task {index}") for index in range(1, task_count + 1))
    return ThingsDiscoveryResult(
        db_path=Path("/tmp/main.sqlite"),
        scope=ThingsScope(kind="inbox"),
        tasks=tasks,
    )


def build_plan(
    entries: tuple[LocalSyncPlanEntry, ...],
    *,
    mutations: tuple[LocalSyncTaskMutation, ...] | None = None,
) -> LocalSyncWritePlan:
    return LocalSyncWritePlan(
        managed_candidates=tuple(entry.candidate for entry in entries),
        entries=entries,
        mutations=mutations if mutations is not None else tuple(entry.mutation for entry in entries if entry.mutation is not None),
    )


def test_main_runs_dry_run_and_logs_summary(monkeypatch, tmp_path):
    config_path = write_config(tmp_path, mode="dry-run")
    logger, handler = build_logger("tests.local_sync_main.dry_run")
    lock_calls: list[Path] = []

    canonical_entry = build_entry(
        uuid="task-1",
        title="Essay",
        classification=CLASSIFICATION_CANONICAL,
        mutation=LocalSyncTaskMutation(
            task_id="task-1",
            title="Essay",
            update_due_date=True,
            due_date=date(2026, 4, 20),
        ),
    )
    diagnostic_entry = build_entry(
        uuid="task-2",
        title="Quiz",
        classification=CLASSIFICATION_DIAGNOSTIC_ONLY,
        mutation=None,
        diagnostics=(NoteDiagnostic(code="malformed_due", message="bad due"),),
    )
    plan = build_plan((canonical_entry, diagnostic_entry))

    @contextmanager
    def fake_lock(config_path: Path):
        lock_calls.append(config_path)
        yield

    monkeypatch.setattr("canvas_things.local_sync_main.setup_local_sync_logger", lambda **kwargs: logger)
    monkeypatch.setattr("canvas_things.local_sync_main.local_sync_lock", fake_lock)
    monkeypatch.setattr(
        "canvas_things.local_sync_main.discover_open_tasks",
        lambda project, move_to_project=None: build_discovery(2),
    )
    monkeypatch.setattr("canvas_things.local_sync_main.build_local_sync_write_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr(
        "canvas_things.local_sync_main.apply_task_mutations",
        lambda mutations: (_ for _ in ()).throw(AssertionError("dry-run should not apply mutations")),
    )

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 0
    assert lock_calls == [config_path]
    assert any("Dry-run mode enabled; skipping Things mutations." in message for message in handler.messages)
    assert any(
        "Local sync summary exit_code=0 mode=dry-run scope=Inbox discovered=2 managed=2 writable=1 canonical=1 redundant_updates=0 diagnostics=1 planned_mutations=1"
        in message
        for message in handler.messages
    )


def test_main_passes_move_to_project_into_inbox_discovery(monkeypatch, tmp_path):
    config_path = write_config(tmp_path, mode="dry-run", move_to_project="School")
    logger, _ = build_logger("tests.local_sync_main.move_to_project_discovery")
    discover_calls: list[tuple[str | None, str | None]] = []

    @contextmanager
    def fake_lock(config_path: Path):
        yield

    monkeypatch.setattr("canvas_things.local_sync_main.setup_local_sync_logger", lambda **kwargs: logger)
    monkeypatch.setattr("canvas_things.local_sync_main.local_sync_lock", fake_lock)

    def fake_discover_open_tasks(project, move_to_project=None):
        discover_calls.append((project, move_to_project))
        return build_discovery(0)

    monkeypatch.setattr("canvas_things.local_sync_main.discover_open_tasks", fake_discover_open_tasks)
    monkeypatch.setattr(
        "canvas_things.local_sync_main.apply_task_mutations",
        lambda mutations: (_ for _ in ()).throw(AssertionError("dry-run should not apply mutations")),
    )

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 0
    assert discover_calls == [(None, "School")]


def test_main_returns_partial_failure_after_apply(monkeypatch, tmp_path):
    config_path = write_config(tmp_path, mode="apply")
    logger, handler = build_logger("tests.local_sync_main.partial")
    guard = FakeTimeoutGuard()

    plan = build_plan(
        (
            build_entry(
                uuid="task-1",
                title="Essay",
                classification=CLASSIFICATION_CANONICAL,
                mutation=LocalSyncTaskMutation(
                    task_id="task-1",
                    title="Essay",
                    update_due_date=True,
                    due_date=date(2026, 4, 20),
                ),
            ),
            build_entry(
                uuid="task-2",
                title="Quiz",
                classification=CLASSIFICATION_CANONICAL,
                mutation=LocalSyncTaskMutation(task_id="task-2", title="Quiz", trash=True),
            ),
        )
    )

    @contextmanager
    def fake_lock(config_path: Path):
        yield

    monkeypatch.setattr("canvas_things.local_sync_main.setup_local_sync_logger", lambda **kwargs: logger)
    monkeypatch.setattr("canvas_things.local_sync_main.local_sync_lock", fake_lock)
    monkeypatch.setattr("canvas_things.local_sync_main.LocalSyncTimeoutGuard.start", lambda timeout: guard)
    monkeypatch.setattr(
        "canvas_things.local_sync_main.discover_open_tasks",
        lambda project, move_to_project=None: build_discovery(2),
    )
    monkeypatch.setattr("canvas_things.local_sync_main.build_local_sync_write_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr(
        "canvas_things.local_sync_main.apply_task_mutations",
        lambda mutations: (
            LocalSyncTaskMutationResult(
                task_id="task-1",
                title="Essay",
                success=True,
                due_date_verified=True,
                due_date_attempts=1,
                project_verified=False,
                project_attempts=0,
                trash_verified=False,
                trash_attempts=0,
                error=None,
            ),
            LocalSyncTaskMutationResult(
                task_id="task-2",
                title="Quiz",
                success=False,
                due_date_verified=False,
                due_date_attempts=0,
                project_verified=False,
                project_attempts=0,
                trash_verified=False,
                trash_attempts=3,
                error="Failed to verify trash move.",
            ),
        ),
    )

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 4
    assert guard.pre_apply_calls == 1
    assert guard.step_calls == ["task-1"]
    assert any("Mutation result task_id=task-2" in message for message in handler.messages)
    assert any("mutation_results=2/2 successes=1 failures=1" in message for message in handler.messages)


def test_main_applies_planner_mutations_in_supplied_order(monkeypatch, tmp_path):
    config_path = write_config(tmp_path, mode="apply")
    logger, _ = build_logger("tests.local_sync_main.mutation_order")
    guard = FakeTimeoutGuard()

    redundant_entry = build_entry(
        uuid="update-1",
        title="[UPDATE] Essay",
        classification="redundant_update",
        mutation=LocalSyncTaskMutation(task_id="update-1", title="[UPDATE] Essay", trash=True),
    )
    canonical_entry = build_entry(
        uuid="base-1",
        title="Essay",
        classification=CLASSIFICATION_CANONICAL,
        mutation=LocalSyncTaskMutation(
            task_id="base-1",
            title="Essay",
            update_due_date=True,
            due_date=date(2026, 4, 20),
        ),
    )
    plan = build_plan(
        (redundant_entry, canonical_entry),
        mutations=(canonical_entry.mutation, redundant_entry.mutation),
    )
    seen_mutation_ids: list[str] = []

    @contextmanager
    def fake_lock(config_path: Path):
        yield

    def fake_apply(mutations):
        seen_mutation_ids.extend(mutation.task_id for mutation in mutations)
        return tuple(
            LocalSyncTaskMutationResult(
                task_id=mutation.task_id,
                title=mutation.title,
                success=True,
                due_date_verified=mutation.update_due_date,
                due_date_attempts=1 if mutation.update_due_date else 0,
                project_verified=False,
                project_attempts=0,
                trash_verified=mutation.trash,
                trash_attempts=1 if mutation.trash else 0,
                error=None,
            )
            for mutation in mutations
        )

    monkeypatch.setattr("canvas_things.local_sync_main.setup_local_sync_logger", lambda **kwargs: logger)
    monkeypatch.setattr("canvas_things.local_sync_main.local_sync_lock", fake_lock)
    monkeypatch.setattr("canvas_things.local_sync_main.LocalSyncTimeoutGuard.start", lambda timeout: guard)
    monkeypatch.setattr(
        "canvas_things.local_sync_main.discover_open_tasks",
        lambda project, move_to_project=None: build_discovery(2),
    )
    monkeypatch.setattr("canvas_things.local_sync_main.build_local_sync_write_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr("canvas_things.local_sync_main.apply_task_mutations", fake_apply)

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 0
    assert seen_mutation_ids == ["base-1", "update-1"]


def test_main_returns_timeout_before_apply_without_running_mutations(monkeypatch, tmp_path):
    config_path = write_config(tmp_path, mode="apply")
    logger, _ = build_logger("tests.local_sync_main.timeout_before_apply")
    guard = FakeTimeoutGuard()
    guard.raise_pre_apply = True
    apply_calls = 0

    plan = build_plan(
        (
            build_entry(
                uuid="task-1",
                title="Essay",
                classification=CLASSIFICATION_CANONICAL,
                mutation=LocalSyncTaskMutation(
                    task_id="task-1",
                    title="Essay",
                    update_due_date=True,
                    due_date=date(2026, 4, 20),
                ),
            ),
        )
    )

    @contextmanager
    def fake_lock(config_path: Path):
        yield

    def fake_apply(mutations):
        nonlocal apply_calls
        apply_calls += 1
        return ()

    monkeypatch.setattr("canvas_things.local_sync_main.setup_local_sync_logger", lambda **kwargs: logger)
    monkeypatch.setattr("canvas_things.local_sync_main.local_sync_lock", fake_lock)
    monkeypatch.setattr("canvas_things.local_sync_main.LocalSyncTimeoutGuard.start", lambda timeout: guard)
    monkeypatch.setattr(
        "canvas_things.local_sync_main.discover_open_tasks",
        lambda project, move_to_project=None: build_discovery(1),
    )
    monkeypatch.setattr("canvas_things.local_sync_main.build_local_sync_write_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr("canvas_things.local_sync_main.apply_task_mutations", fake_apply)

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 5
    assert apply_calls == 0
    assert guard.pre_apply_calls == 1


def test_main_stops_result_processing_when_timeout_hits_between_results(monkeypatch, tmp_path):
    config_path = write_config(tmp_path, mode="apply")
    logger, handler = build_logger("tests.local_sync_main.timeout_between_results")
    guard = FakeTimeoutGuard(error_on_step="task-1")

    plan = build_plan(
        (
            build_entry(
                uuid="task-1",
                title="Essay",
                classification=CLASSIFICATION_CANONICAL,
                mutation=LocalSyncTaskMutation(
                    task_id="task-1",
                    title="Essay",
                    update_due_date=True,
                    due_date=date(2026, 4, 20),
                ),
            ),
            build_entry(
                uuid="task-2",
                title="Quiz",
                classification=CLASSIFICATION_CANONICAL,
                mutation=LocalSyncTaskMutation(task_id="task-2", title="Quiz", trash=True),
            ),
            build_entry(
                uuid="task-3",
                title="Lab",
                classification=CLASSIFICATION_CANONICAL,
                mutation=LocalSyncTaskMutation(task_id="task-3", title="Lab", trash=True),
            ),
        )
    )

    @contextmanager
    def fake_lock(config_path: Path):
        yield

    monkeypatch.setattr("canvas_things.local_sync_main.setup_local_sync_logger", lambda **kwargs: logger)
    monkeypatch.setattr("canvas_things.local_sync_main.local_sync_lock", fake_lock)
    monkeypatch.setattr("canvas_things.local_sync_main.LocalSyncTimeoutGuard.start", lambda timeout: guard)
    monkeypatch.setattr(
        "canvas_things.local_sync_main.discover_open_tasks",
        lambda project, move_to_project=None: build_discovery(3),
    )
    monkeypatch.setattr("canvas_things.local_sync_main.build_local_sync_write_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr(
        "canvas_things.local_sync_main.apply_task_mutations",
        lambda mutations: tuple(
            LocalSyncTaskMutationResult(
                task_id=f"task-{index}",
                title=title,
                success=True,
                due_date_verified=False,
                due_date_attempts=0,
                project_verified=False,
                project_attempts=0,
                trash_verified=index != 1,
                trash_attempts=1 if index != 1 else 0,
                error=None,
            )
            for index, title in ((1, "Essay"), (2, "Quiz"), (3, "Lab"))
        ),
    )

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 5
    assert guard.step_calls == ["task-1"]
    assert any("Mutation result task_id=task-1" in message for message in handler.messages)
    assert not any("Mutation result task_id=task-2" in message for message in handler.messages)
    assert any("mutation_results=1/3 successes=1 failures=0" in message for message in handler.messages)


def test_main_returns_config_error_for_missing_config(tmp_path):
    missing_path = tmp_path / "missing.yml"

    assert main(["--config", str(missing_path)]) == 2
