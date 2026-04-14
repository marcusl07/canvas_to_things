"""End-to-end command runner for the local Things deadline sync companion."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Iterable, Sequence

from .local_sync_applescript import (
    LocalSyncAppleScriptError,
    LocalSyncTaskMutation,
    LocalSyncTaskMutationResult,
    apply_task_mutations,
)
from .local_sync_cli import load_settings_from_argv
from .local_sync_config import LocalSyncConfigError, LocalSyncExitCode, LocalSyncSettings
from .local_sync_logging import DEFAULT_LOGGER_NAME, setup_local_sync_logger
from .local_sync_planner import (
    CLASSIFICATION_CANONICAL,
    CLASSIFICATION_DIAGNOSTIC_ONLY,
    CLASSIFICATION_REDUNDANT_UPDATE,
    LocalSyncPlannerError,
    LocalSyncWritePlan,
    build_local_sync_write_plan,
)
from .local_sync_runtime import (
    LocalSyncRuntimeError,
    LocalSyncTimeoutError,
    LocalSyncTimeoutGuard,
    local_sync_lock,
)
from .local_sync_things_db import LocalSyncThingsDBError, ThingsDiscoveryResult, discover_open_tasks

PRECONDITION_ERRORS = (
    LocalSyncAppleScriptError,
    LocalSyncPlannerError,
    LocalSyncRuntimeError,
    LocalSyncThingsDBError,
)


@dataclass(frozen=True)
class LocalSyncRunSummary:
    """Final run accounting emitted at the end of each local-sync command."""

    discovered_task_count: int
    managed_task_count: int
    writable_task_count: int
    canonical_task_count: int
    redundant_update_count: int
    diagnostic_only_count: int
    planned_mutation_count: int
    processed_result_count: int
    total_result_count: int
    mutation_success_count: int
    mutation_failure_count: int


def main(argv: Iterable[str] | None = None) -> int:
    """Run one local-sync command and return a stable process exit code."""

    try:
        _, settings = load_settings_from_argv(argv)
    except SystemExit as exc:
        return _system_exit_code(exc)
    except LocalSyncConfigError as exc:
        _write_stderr(f"Configuration error: {exc}")
        return int(LocalSyncExitCode.CONFIG_ERROR)

    try:
        logger = setup_local_sync_logger(logger_name=DEFAULT_LOGGER_NAME)
    except OSError as exc:
        _write_stderr(f"Failed to initialize local-sync logging: {exc}")
        return int(LocalSyncExitCode.UNEXPECTED_ERROR)

    discovery: ThingsDiscoveryResult | None = None
    plan: LocalSyncWritePlan | None = None
    processed_results: list[LocalSyncTaskMutationResult] = []
    total_result_count = 0

    try:
        _log_run_start(logger, settings)
        timeout_guard = LocalSyncTimeoutGuard.start(settings.timeout_seconds)
        with local_sync_lock(settings.config_path):
            discovery = discover_open_tasks(
                settings.project,
                move_to_project=settings.move_to_project if settings.project is None else None,
            )
            logger.info(
                "Discovered %s open tasks from %s using %s.",
                len(discovery.tasks),
                _format_scope(discovery),
                discovery.db_path,
            )

            plan = build_local_sync_write_plan(
                discovery.tasks,
                candidate_cap=settings.candidate_cap,
                move_to_project=settings.move_to_project,
            )
            _log_plan_details(logger, plan)

            if settings.dry_run:
                logger.info("Dry-run mode enabled; skipping Things mutations.")
                return _finish_run(
                    logger,
                    settings,
                    discovery,
                    plan,
                    processed_results=processed_results,
                    total_result_count=0,
                    exit_code=LocalSyncExitCode.SUCCESS,
                )

            if not plan.mutations:
                logger.info("Apply mode selected, but no mutations are required.")
                return _finish_run(
                    logger,
                    settings,
                    discovery,
                    plan,
                    processed_results=processed_results,
                    total_result_count=0,
                    exit_code=LocalSyncExitCode.SUCCESS,
                )

            timeout_guard.check_pre_apply()
            results = apply_task_mutations(plan.mutations)
            total_result_count = len(results)

            for index, result in enumerate(results):
                processed_results.append(result)
                _log_mutation_result(logger, result)
                if index < len(results) - 1:
                    timeout_guard.check_result_step_boundary(result.task_id)

            exit_code = LocalSyncExitCode.SUCCESS
            if any(not result.success for result in processed_results):
                exit_code = LocalSyncExitCode.PARTIAL_FAILURE

            return _finish_run(
                logger,
                settings,
                discovery,
                plan,
                processed_results=processed_results,
                total_result_count=total_result_count,
                exit_code=exit_code,
            )
    except LocalSyncTimeoutError as exc:
        _emit_cli_error(logger, str(exc))
        return _finish_after_error(
            logger,
            settings,
            discovery,
            plan,
            processed_results=processed_results,
            total_result_count=total_result_count,
            exit_code=LocalSyncExitCode.TIMEOUT,
        )
    except PRECONDITION_ERRORS as exc:
        _emit_cli_error(logger, str(exc))
        return int(LocalSyncExitCode.PRECONDITION_ERROR)
    except Exception:
        logger.exception("Unexpected local-sync failure.")
        _write_stderr("Unexpected local-sync failure. See the local-sync log for details.")
        return int(LocalSyncExitCode.UNEXPECTED_ERROR)


def _finish_run(
    logger: logging.Logger,
    settings: LocalSyncSettings,
    discovery: ThingsDiscoveryResult,
    plan: LocalSyncWritePlan,
    *,
    processed_results: Sequence[LocalSyncTaskMutationResult],
    total_result_count: int,
    exit_code: LocalSyncExitCode,
) -> int:
    _log_summary(
        logger,
        settings,
        discovery,
        plan,
        processed_results=processed_results,
        total_result_count=total_result_count,
        exit_code=exit_code,
    )
    return int(exit_code)


def _finish_after_error(
    logger: logging.Logger,
    settings: LocalSyncSettings,
    discovery: ThingsDiscoveryResult | None,
    plan: LocalSyncWritePlan | None,
    *,
    processed_results: Sequence[LocalSyncTaskMutationResult],
    total_result_count: int,
    exit_code: LocalSyncExitCode,
) -> int:
    if discovery is not None and plan is not None:
        _log_summary(
            logger,
            settings,
            discovery,
            plan,
            processed_results=processed_results,
            total_result_count=total_result_count,
            exit_code=exit_code,
        )
    return int(exit_code)


def _log_run_start(logger: logging.Logger, settings: LocalSyncSettings) -> None:
    logger.info(
        "Starting local sync mode=%s scope=%s move_to_project=%s candidate_cap=%s timeout_seconds=%.1f config=%s",
        settings.mode,
        settings.project if settings.project is not None else "Inbox",
        settings.move_to_project if settings.move_to_project is not None else "-",
        settings.candidate_cap,
        settings.timeout_seconds,
        settings.config_path,
    )


def _log_plan_details(logger: logging.Logger, plan: LocalSyncWritePlan) -> None:
    for entry in plan.entries:
        diagnostics = ",".join(diagnostic.code for diagnostic in entry.candidate.parsed_note.diagnostics)
        if entry.classification == CLASSIFICATION_DIAGNOSTIC_ONLY:
            logger.warning(
                "Diagnostic-only managed task task_id=%s title=%r diagnostics=%s",
                entry.candidate.task.uuid,
                entry.candidate.task.title,
                diagnostics or "none",
            )

    for mutation in plan.mutations:
        logger.info(
            "Planned mutation task_id=%s title=%r actions=%s",
            mutation.task_id,
            mutation.title,
            _format_mutation_actions(mutation),
        )


def _log_mutation_result(logger: logging.Logger, result: LocalSyncTaskMutationResult) -> None:
    level = logging.INFO if result.success else logging.WARNING
    logger.log(
        level,
        "Mutation result task_id=%s title=%r success=%s due_attempts=%s project_attempts=%s trash_attempts=%s error=%s",
        result.task_id,
        result.title,
        result.success,
        result.due_date_attempts,
        result.project_attempts,
        result.trash_attempts,
        result.error if result.error is not None else "-",
    )


def _log_summary(
    logger: logging.Logger,
    settings: LocalSyncSettings,
    discovery: ThingsDiscoveryResult,
    plan: LocalSyncWritePlan,
    *,
    processed_results: Sequence[LocalSyncTaskMutationResult],
    total_result_count: int,
    exit_code: LocalSyncExitCode,
) -> None:
    summary = _build_summary(
        discovery,
        plan,
        processed_results=processed_results,
        total_result_count=total_result_count,
    )
    result_fragment = ""
    if summary.total_result_count > 0:
        result_fragment = (
            f" mutation_results={summary.processed_result_count}/{summary.total_result_count}"
            f" successes={summary.mutation_success_count}"
            f" failures={summary.mutation_failure_count}"
        )
    logger.info(
        "Local sync summary exit_code=%s mode=%s scope=%s discovered=%s managed=%s writable=%s canonical=%s redundant_updates=%s diagnostics=%s planned_mutations=%s%s",
        int(exit_code),
        settings.mode,
        _format_scope(discovery),
        summary.discovered_task_count,
        summary.managed_task_count,
        summary.writable_task_count,
        summary.canonical_task_count,
        summary.redundant_update_count,
        summary.diagnostic_only_count,
        summary.planned_mutation_count,
        result_fragment,
    )


def _build_summary(
    discovery: ThingsDiscoveryResult,
    plan: LocalSyncWritePlan,
    *,
    processed_results: Sequence[LocalSyncTaskMutationResult],
    total_result_count: int,
) -> LocalSyncRunSummary:
    return LocalSyncRunSummary(
        discovered_task_count=len(discovery.tasks),
        managed_task_count=plan.managed_task_count,
        writable_task_count=plan.writable_task_count,
        canonical_task_count=sum(
            1 for entry in plan.entries if entry.classification == CLASSIFICATION_CANONICAL
        ),
        redundant_update_count=sum(
            1 for entry in plan.entries if entry.classification == CLASSIFICATION_REDUNDANT_UPDATE
        ),
        diagnostic_only_count=sum(
            1 for entry in plan.entries if entry.classification == CLASSIFICATION_DIAGNOSTIC_ONLY
        ),
        planned_mutation_count=len(plan.mutations),
        processed_result_count=len(processed_results),
        total_result_count=total_result_count,
        mutation_success_count=sum(1 for result in processed_results if result.success),
        mutation_failure_count=sum(1 for result in processed_results if not result.success),
    )


def _format_scope(discovery: ThingsDiscoveryResult) -> str:
    if discovery.scope.kind == "project" and discovery.scope.project_title is not None:
        return f"project:{discovery.scope.project_title}"
    return "Inbox"


def _format_mutation_actions(mutation: LocalSyncTaskMutation) -> str:
    actions: list[str] = []
    if mutation.update_due_date:
        actions.append(f"due_date={mutation.due_date.isoformat() if mutation.due_date else 'clear'}")
    if mutation.project_target is not None:
        actions.append(
            f"project={mutation.project_target.name or mutation.project_target.project_id}"
        )
    if mutation.move_to_inbox:
        actions.append("move_to_inbox")
    if mutation.trash:
        actions.append("trash")
    return ",".join(actions)


def _emit_cli_error(logger: logging.Logger, message: str) -> None:
    logger.error(message)
    _write_stderr(message)


def _write_stderr(message: str) -> None:
    print(message, file=sys.stderr)


def _system_exit_code(exc: SystemExit) -> int:
    if isinstance(exc.code, int):
        return exc.code
    return int(LocalSyncExitCode.CONFIG_ERROR)


__all__ = ["LocalSyncRunSummary", "main"]


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
