"""AppleScript-backed Things mutation runner for local sync."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import date
from typing import Callable, Sequence

VERIFY_ATTEMPTS = 3
VERIFY_DELAY_SECONDS = 0.5


class LocalSyncAppleScriptError(RuntimeError):
    """Raised when the AppleScript write batch cannot be executed safely."""


@dataclass(frozen=True)
class LocalSyncProjectTarget:
    """Resolved Things project target for a task move."""

    name: str | None = None
    project_id: str | None = None

    def __post_init__(self) -> None:
        cleaned_name = _clean_optional_text(self.name, field_name="name")
        cleaned_project_id = _clean_optional_text(self.project_id, field_name="project_id")
        object.__setattr__(self, "name", cleaned_name)
        object.__setattr__(self, "project_id", cleaned_project_id)
        if cleaned_name is None and cleaned_project_id is None:
            raise ValueError("LocalSyncProjectTarget requires a project name or project_id.")


@dataclass(frozen=True)
class LocalSyncTaskMutation:
    """One task-level mutation bundle to apply in Things."""

    task_id: str
    title: str
    update_due_date: bool = False
    due_date: date | None = None
    update_schedule_date: bool = False
    schedule_date: date | None = None
    update_title: bool = False
    new_title: str | None = None
    project_target: LocalSyncProjectTarget | None = None
    move_to_inbox: bool = False
    trash: bool = False

    def __post_init__(self) -> None:
        task_id = _require_text(self.task_id, field_name="task_id")
        title = _require_text(self.title, field_name="title")
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "title", title)

        if self.update_due_date is False and self.due_date is not None:
            raise ValueError("due_date requires update_due_date=True.")
        if self.update_schedule_date is False and self.schedule_date is not None:
            raise ValueError("schedule_date requires update_schedule_date=True.")
        if self.update_title is False and self.new_title is not None:
            raise ValueError("new_title requires update_title=True.")
        if self.update_title and self.new_title is None:
            raise ValueError("update_title requires new_title.")
        if self.new_title is not None:
            object.__setattr__(self, "new_title", _require_text(self.new_title, field_name="new_title"))
        if self.project_target is not None and self.move_to_inbox:
            raise ValueError("project_target and move_to_inbox cannot both be set.")
        if not any(
            (
                self.update_due_date,
                self.update_schedule_date,
                self.update_title,
                self.project_target is not None,
                self.move_to_inbox,
                self.trash,
            )
        ):
            raise ValueError("LocalSyncTaskMutation requires at least one mutation.")


@dataclass(frozen=True)
class AppleScriptExecutionResult:
    """Captured `osascript` execution output."""

    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class LocalSyncTaskMutationResult:
    """Per-task mutation outcome returned from the AppleScript batch."""

    task_id: str
    title: str
    success: bool
    due_date_verified: bool
    due_date_attempts: int
    schedule_verified: bool
    schedule_attempts: int
    title_verified: bool
    title_attempts: int
    project_verified: bool
    project_attempts: int
    trash_verified: bool
    trash_attempts: int
    error: str | None = None


@dataclass(frozen=True)
class LocalSyncTaskNoteUpdate:
    """One note-only update to apply to an existing Things task."""

    task_id: str
    title: str
    note: str

    def __post_init__(self) -> None:
        task_id = _require_text(self.task_id, field_name="task_id")
        title = _require_text(self.title, field_name="title")
        note = _require_script_text(self.note, field_name="note")
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "note", note)


@dataclass(frozen=True)
class LocalSyncTaskNoteUpdateResult:
    """Per-task note update outcome returned from the AppleScript batch."""

    task_id: str
    title: str
    success: bool
    notes_verified: bool
    notes_attempts: int
    error: str | None = None


AppleScriptRunner = Callable[[str], AppleScriptExecutionResult]


def apply_task_mutations(
    mutations: Sequence[LocalSyncTaskMutation],
    *,
    runner: AppleScriptRunner | None = None,
) -> tuple[LocalSyncTaskMutationResult, ...]:
    """Apply a batch of Things task mutations with a single `osascript` run."""

    if not mutations:
        return ()

    script = build_apply_task_mutations_script(mutations)
    execution = (runner or run_osascript)(script)
    if execution.returncode != 0:
        stderr = execution.stderr.strip()
        stdout = execution.stdout.strip()
        details = stderr or stdout or "osascript exited without output."
        raise LocalSyncAppleScriptError(f"AppleScript batch failed: {details}")
    return parse_task_mutation_results(execution.stdout, expected_mutations=mutations)


def apply_task_note_updates(
    note_updates: Sequence[LocalSyncTaskNoteUpdate],
    *,
    runner: AppleScriptRunner | None = None,
) -> tuple[LocalSyncTaskNoteUpdateResult, ...]:
    """Apply a batch of Things note updates with a single `osascript` run."""

    if not note_updates:
        return ()

    script = build_apply_task_note_updates_script(note_updates)
    execution = (runner or run_osascript)(script)
    if execution.returncode != 0:
        stderr = execution.stderr.strip()
        stdout = execution.stdout.strip()
        details = stderr or stdout or "osascript exited without output."
        raise LocalSyncAppleScriptError(f"AppleScript batch failed: {details}")
    return parse_task_note_update_results(execution.stdout, expected_note_updates=note_updates)


def run_osascript(script: str) -> AppleScriptExecutionResult:
    """Execute an AppleScript program via `osascript`."""

    completed = subprocess.run(
        ["osascript", "-"],
        input=script,
        capture_output=True,
        check=False,
        text=True,
    )
    return AppleScriptExecutionResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def build_apply_task_mutations_script(mutations: Sequence[LocalSyncTaskMutation]) -> str:
    """Build the single-run AppleScript used to apply one mutation batch."""

    lines = [
        'using terms from application "Things3"',
        "set resultItems to {}",
        "",
        "on replaceText(sourceText, searchText, replacementText)",
        "    set previousDelimiters to AppleScript's text item delimiters",
        "    set AppleScript's text item delimiters to searchText",
        "    set textItems to text items of sourceText",
        "    set AppleScript's text item delimiters to replacementText",
        "    set replacedText to textItems as text",
        "    set AppleScript's text item delimiters to previousDelimiters",
        "    return replacedText",
        "end replaceText",
        "",
        "on jsonEscape(sourceText)",
        "    set escapedText to sourceText as text",
        "    set escapedText to my replaceText(escapedText, \"\\\\\", \"\\\\\\\\\")",
        "    set escapedText to my replaceText(escapedText, quote, \"\\\\\" & quote)",
        "    set escapedText to my replaceText(escapedText, return, \"\\\\r\")",
        "    set escapedText to my replaceText(escapedText, linefeed, \"\\\\n\")",
        "    set escapedText to my replaceText(escapedText, tab, \"\\\\t\")",
        "    return quote & escapedText & quote",
        "end jsonEscape",
        "",
        "on jsonBoolean(flagValue)",
        "    if flagValue then",
        "        return \"true\"",
        "    end if",
        "    return \"false\"",
        "end jsonBoolean",
        "",
        "on jsonMaybeString(fieldValue)",
        "    if fieldValue is missing value then",
        "        return \"null\"",
        "    end if",
        "    return my jsonEscape(fieldValue as text)",
        "end jsonMaybeString",
        "",
        "on joinJson(jsonItems)",
        "    if (count of jsonItems) is 0 then",
        "        return \"\"",
        "    end if",
        "    set previousDelimiters to AppleScript's text item delimiters",
        "    set AppleScript's text item delimiters to \",\"",
        "    set joinedText to jsonItems as text",
        "    set AppleScript's text item delimiters to previousDelimiters",
        "    return joinedText",
        "end joinJson",
        "",
        "on dateMatches(actualDate, expectedDate)",
        "    if actualDate is missing value or expectedDate is missing value then",
        "        return (actualDate is missing value) and (expectedDate is missing value)",
        "    end if",
        "    return (year of actualDate is year of expectedDate) and (month of actualDate is month of expectedDate) and (day of actualDate is day of expectedDate)",
        "end dateMatches",
        "",
        f"on verifyProjectAssignment(taskRef, targetProject, targetProjectId)",
        f"    repeat with attemptIndex from 1 to {VERIFY_ATTEMPTS}",
        "        tell application \"Things3\"",
        "            set project of taskRef to targetProject",
        "            set actualProject to (project of taskRef)",
        "            if actualProject is not missing value then",
        "                if id of actualProject is targetProjectId then",
        "                    return attemptIndex as integer",
        "                end if",
        "            end if",
        "        end tell",
        f"        if (attemptIndex as integer) < {VERIFY_ATTEMPTS} then delay {VERIFY_DELAY_SECONDS}",
        "    end repeat",
        f"    error \"Failed to verify project write after {VERIFY_ATTEMPTS} attempts.\"",
        "end verifyProjectAssignment",
        "",
        "on buildResultJson(taskId, titleText, successFlag, dueVerified, dueAttempts, scheduleVerified, scheduleAttempts, titleVerified, titleAttempts, projectVerified, projectAttempts, trashVerified, trashAttempts, errorText)",
        "    return \"{\" & ¬",
        "        my jsonEscape(\"task_id\") & \":\" & my jsonEscape(taskId) & \",\" & ¬",
        "        my jsonEscape(\"title\") & \":\" & my jsonEscape(titleText) & \",\" & ¬",
        "        my jsonEscape(\"success\") & \":\" & my jsonBoolean(successFlag) & \",\" & ¬",
        "        my jsonEscape(\"due_date_verified\") & \":\" & my jsonBoolean(dueVerified) & \",\" & ¬",
        "        my jsonEscape(\"due_date_attempts\") & \":\" & (dueAttempts as text) & \",\" & ¬",
        "        my jsonEscape(\"schedule_verified\") & \":\" & my jsonBoolean(scheduleVerified) & \",\" & ¬",
        "        my jsonEscape(\"schedule_attempts\") & \":\" & (scheduleAttempts as text) & \",\" & ¬",
        "        my jsonEscape(\"title_verified\") & \":\" & my jsonBoolean(titleVerified) & \",\" & ¬",
        "        my jsonEscape(\"title_attempts\") & \":\" & (titleAttempts as text) & \",\" & ¬",
        "        my jsonEscape(\"project_verified\") & \":\" & my jsonBoolean(projectVerified) & \",\" & ¬",
        "        my jsonEscape(\"project_attempts\") & \":\" & (projectAttempts as text) & \",\" & ¬",
        "        my jsonEscape(\"trash_verified\") & \":\" & my jsonBoolean(trashVerified) & \",\" & ¬",
        "        my jsonEscape(\"trash_attempts\") & \":\" & (trashAttempts as text) & \",\" & ¬",
        "        my jsonEscape(\"error\") & \":\" & my jsonMaybeString(errorText) & ¬",
        "        \"}\"",
        "end buildResultJson",
        "",
    ]
    for mutation in mutations:
        lines.extend(_build_task_block(mutation))
    lines.extend(
        [
            'return "[" & my joinJson(resultItems) & "]"',
            'end using terms from',
            "",
        ]
    )
    return "\n".join(lines)


def build_apply_task_note_updates_script(note_updates: Sequence[LocalSyncTaskNoteUpdate]) -> str:
    """Build the single-run AppleScript used to apply one note-update batch."""

    lines = [
        'using terms from application "Things3"',
        "set resultItems to {}",
        "",
        "on replaceText(sourceText, searchText, replacementText)",
        "    set previousDelimiters to AppleScript's text item delimiters",
        "    set AppleScript's text item delimiters to searchText",
        "    set textItems to text items of sourceText",
        "    set AppleScript's text item delimiters to replacementText",
        "    set replacedText to textItems as text",
        "    set AppleScript's text item delimiters to previousDelimiters",
        "    return replacedText",
        "end replaceText",
        "",
        "on jsonEscape(sourceText)",
        "    set escapedText to sourceText as text",
        "    set escapedText to my replaceText(escapedText, \"\\\\\", \"\\\\\\\\\")",
        "    set escapedText to my replaceText(escapedText, quote, \"\\\\\" & quote)",
        "    set escapedText to my replaceText(escapedText, return, \"\\\\r\")",
        "    set escapedText to my replaceText(escapedText, linefeed, \"\\\\n\")",
        "    set escapedText to my replaceText(escapedText, tab, \"\\\\t\")",
        "    return quote & escapedText & quote",
        "end jsonEscape",
        "",
        "on jsonBoolean(flagValue)",
        "    if flagValue then",
        "        return \"true\"",
        "    end if",
        "    return \"false\"",
        "end jsonBoolean",
        "",
        "on jsonMaybeString(fieldValue)",
        "    if fieldValue is missing value then",
        "        return \"null\"",
        "    end if",
        "    return my jsonEscape(fieldValue as text)",
        "end jsonMaybeString",
        "",
        "on joinJson(jsonItems)",
        "    if (count of jsonItems) is 0 then",
        "        return \"\"",
        "    end if",
        "    set previousDelimiters to AppleScript's text item delimiters",
        "    set AppleScript's text item delimiters to \",\"",
        "    set joinedText to jsonItems as text",
        "    set AppleScript's text item delimiters to previousDelimiters",
        "    return joinedText",
        "end joinJson",
        "",
        "on normalizeNewlines(sourceText)",
        "    set normalizedText to sourceText as text",
        "    set normalizedText to my replaceText(normalizedText, return, linefeed)",
        "    return normalizedText",
        "end normalizeNewlines",
        "",
        "on buildNoteResultJson(taskId, titleText, successFlag, notesVerified, notesAttempts, errorText)",
        "    return \"{\" & ¬",
        "        my jsonEscape(\"task_id\") & \":\" & my jsonEscape(taskId) & \",\" & ¬",
        "        my jsonEscape(\"title\") & \":\" & my jsonEscape(titleText) & \",\" & ¬",
        "        my jsonEscape(\"success\") & \":\" & my jsonBoolean(successFlag) & \",\" & ¬",
        "        my jsonEscape(\"notes_verified\") & \":\" & my jsonBoolean(notesVerified) & \",\" & ¬",
        "        my jsonEscape(\"notes_attempts\") & \":\" & (notesAttempts as text) & \",\" & ¬",
        "        my jsonEscape(\"error\") & \":\" & my jsonMaybeString(errorText) & ¬",
        "        \"}\"",
        "end buildNoteResultJson",
        "",
    ]
    for note_update in note_updates:
        lines.extend(_build_note_update_block(note_update))
    lines.extend(
        [
            'return "[" & my joinJson(resultItems) & "]"',
            'end using terms from',
            "",
        ]
    )
    return "\n".join(lines)


def parse_task_mutation_results(
    output: str,
    *,
    expected_mutations: Sequence[LocalSyncTaskMutation],
) -> tuple[LocalSyncTaskMutationResult, ...]:
    """Parse and validate JSON results emitted by the AppleScript batch."""

    raw_output = output.strip()
    if not raw_output:
        raise LocalSyncAppleScriptError("AppleScript batch produced no output.")

    try:
        payload = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise LocalSyncAppleScriptError("AppleScript batch produced invalid JSON output.") from exc

    if not isinstance(payload, list):
        raise LocalSyncAppleScriptError("AppleScript batch output must be a JSON array.")

    results = tuple(_parse_result_entry(entry) for entry in payload)
    _validate_result_sequence(results, expected_mutations)
    return results


def parse_task_note_update_results(
    output: str,
    *,
    expected_note_updates: Sequence[LocalSyncTaskNoteUpdate],
) -> tuple[LocalSyncTaskNoteUpdateResult, ...]:
    """Parse and validate JSON results emitted by the note-update AppleScript batch."""

    raw_output = output.strip()
    if not raw_output:
        raise LocalSyncAppleScriptError("AppleScript batch produced no output.")

    try:
        payload = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise LocalSyncAppleScriptError("AppleScript batch produced invalid JSON output.") from exc

    if not isinstance(payload, list):
        raise LocalSyncAppleScriptError("AppleScript batch output must be a JSON array.")

    results = tuple(_parse_note_update_result_entry(entry) for entry in payload)
    _validate_note_update_result_sequence(results, expected_note_updates)
    return results


def _build_task_block(mutation: LocalSyncTaskMutation) -> list[str]:
    lines = [
        f"set dueAttempts to 0",
        f"set scheduleAttempts to 0",
        f"set titleAttempts to 0",
        f"set projectAttempts to 0",
        f"set trashAttempts to 0",
        f"set dueVerified to false",
        f"set scheduleVerified to false",
        f"set titleVerified to false",
        f"set projectVerified to false",
        f"set trashVerified to false",
        f"set errorText to missing value",
        "try",
        "    tell application \"Things3\"",
        f'        set taskRef to (to do id {_apple_script_string(mutation.task_id)})',
        "    end tell",
    ]

    if mutation.update_due_date:
        due_expression = "missing value"
        if mutation.due_date is not None:
            due_expression = _apple_script_date_literal(mutation.due_date)
        lines.extend(
            [
                f"    repeat with attemptIndex from 1 to {VERIFY_ATTEMPTS}",
                "        tell application \"Things3\"",
                f"            set due date of taskRef to {due_expression}",
                f"            if my dateMatches((due date of taskRef), {due_expression}) then",
                "                set dueAttempts to attemptIndex as integer",
                "                set dueVerified to true",
                "                exit repeat",
                "            end if",
                "        end tell",
                f"        if (attemptIndex as integer) < {VERIFY_ATTEMPTS} then delay {VERIFY_DELAY_SECONDS}",
                "    end repeat",
                "    if dueVerified is false then",
                f"        error \"Failed to verify due date write after {VERIFY_ATTEMPTS} attempts.\"",
                "    end if",
            ]
        )

    if mutation.update_schedule_date:
        if mutation.schedule_date is None:
            lines.extend(
                [
                    f"    repeat with attemptIndex from 1 to {VERIFY_ATTEMPTS}",
                    "        tell application \"Things3\"",
                    "            move taskRef to list \"Anytime\"",
                    "            if activation date of taskRef is missing value then",
                    "                set scheduleAttempts to attemptIndex as integer",
                    "                set scheduleVerified to true",
                    "                exit repeat",
                    "            end if",
                    "        end tell",
                    f"        if (attemptIndex as integer) < {VERIFY_ATTEMPTS} then delay {VERIFY_DELAY_SECONDS}",
                    "    end repeat",
                    "    if scheduleVerified is false then",
                    f"        error \"Failed to verify schedule write after {VERIFY_ATTEMPTS} attempts.\"",
                    "    end if",
                ]
            )
        else:
            schedule_expression = _apple_script_date_literal(mutation.schedule_date)
            lines.extend(
                [
                    f"    repeat with attemptIndex from 1 to {VERIFY_ATTEMPTS}",
                    "        tell application \"Things3\"",
                    f"            schedule taskRef for {schedule_expression}",
                    f"            if my dateMatches((activation date of taskRef), {schedule_expression}) then",
                    "                set scheduleAttempts to attemptIndex as integer",
                    "                set scheduleVerified to true",
                    "                exit repeat",
                    "            end if",
                    "        end tell",
                    f"        if (attemptIndex as integer) < {VERIFY_ATTEMPTS} then delay {VERIFY_DELAY_SECONDS}",
                    "    end repeat",
                    "    if scheduleVerified is false then",
                    f"        error \"Failed to verify schedule write after {VERIFY_ATTEMPTS} attempts.\"",
                    "    end if",
                ]
            )

    if mutation.update_title:
        title_expression = _apple_script_string(mutation.new_title or "")
        lines.extend(
            [
                f"    repeat with attemptIndex from 1 to {VERIFY_ATTEMPTS}",
                "        tell application \"Things3\"",
                f"            set name of taskRef to {title_expression}",
                f"            if name of taskRef is {title_expression} then",
                "                set titleAttempts to attemptIndex as integer",
                "                set titleVerified to true",
                "                exit repeat",
                "            end if",
                "        end tell",
                f"        if (attemptIndex as integer) < {VERIFY_ATTEMPTS} then delay {VERIFY_DELAY_SECONDS}",
                "    end repeat",
                "    if titleVerified is false then",
                f"        error \"Failed to verify title write after {VERIFY_ATTEMPTS} attempts.\"",
                "    end if",
            ]
        )

    if mutation.project_target is not None:
        project_resolution_lines: list[str]
        if mutation.project_target.project_id is not None:
            project_resolution_lines = [
                "    tell application \"Things3\"",
                f"        set targetProject to (project id {_apple_script_string(mutation.project_target.project_id)})",
                "        set targetProjectId to id of targetProject",
                "    end tell",
            ]
        else:
            project_name = _apple_script_string(mutation.project_target.name or "")
            project_resolution_lines = [
                "    tell application \"Things3\"",
                f"        set matchingProjects to (every project whose name is {project_name})",
                "        if (count of matchingProjects) is 0 then",
                f"            error \"Target project not found: \" & {project_name}",
                "        end if",
                "        if (count of matchingProjects) is greater than 1 then",
                f"            error \"Target project name is ambiguous: \" & {project_name}",
                "        end if",
                "        set targetProject to item 1 of matchingProjects",
                "        set targetProjectId to id of targetProject",
                "    end tell",
            ]
        lines.extend(
            project_resolution_lines
            + [
                "    set projectAttempts to my verifyProjectAssignment(taskRef, targetProject, targetProjectId)",
                "    set projectVerified to true",
            ]
        )
    elif mutation.move_to_inbox:
        lines.extend(
            [
                f"    repeat with attemptIndex from 1 to {VERIFY_ATTEMPTS}",
                "        tell application \"Things3\"",
                "            move taskRef to list \"Inbox\"",
                f"            set inboxMatches to (every to do of list \"Inbox\" whose id is {_apple_script_string(mutation.task_id)})",
                "            if (project of taskRef is missing value) and ((count of inboxMatches) is greater than 0) then",
                "                set projectAttempts to attemptIndex as integer",
                "                set projectVerified to true",
                "                exit repeat",
                "            end if",
                "        end tell",
                f"        if (attemptIndex as integer) < {VERIFY_ATTEMPTS} then delay {VERIFY_DELAY_SECONDS}",
                "    end repeat",
                "    if projectVerified is false then",
                f"        error \"Failed to verify Inbox move after {VERIFY_ATTEMPTS} attempts.\"",
                "    end if",
            ]
        )

    if mutation.trash:
        lines.extend(
            [
                f"    repeat with attemptIndex from 1 to {VERIFY_ATTEMPTS}",
                "        tell application \"Things3\"",
                "            move taskRef to list \"Trash\"",
                f"            set trashMatches to (every to do of list \"Trash\" whose id is {_apple_script_string(mutation.task_id)})",
                "            if (count of trashMatches) is greater than 0 then",
                "                set trashAttempts to attemptIndex as integer",
                "                set trashVerified to true",
                "                exit repeat",
                "            end if",
                "        end tell",
                f"        if (attemptIndex as integer) < {VERIFY_ATTEMPTS} then delay {VERIFY_DELAY_SECONDS}",
                "    end repeat",
                "    if trashVerified is false then",
                f"        error \"Failed to verify trash move after {VERIFY_ATTEMPTS} attempts.\"",
                "    end if",
            ]
        )

    lines.extend(
        [
            "on error errorMessage number errorNumber",
            f"    set errorText to errorMessage & \" (\" & (errorNumber as text) & \")\"",
            "end try",
            "if errorText is not missing value then",
            f"    copy my buildResultJson({_apple_script_string(mutation.task_id)}, {_apple_script_string(mutation.title)}, false, dueVerified, dueAttempts, scheduleVerified, scheduleAttempts, titleVerified, titleAttempts, projectVerified, projectAttempts, trashVerified, trashAttempts, errorText) to end of resultItems",
            "    set errorText to missing value",
            "else",
            f"    copy my buildResultJson({_apple_script_string(mutation.task_id)}, {_apple_script_string(mutation.title)}, true, dueVerified, dueAttempts, scheduleVerified, scheduleAttempts, titleVerified, titleAttempts, projectVerified, projectAttempts, trashVerified, trashAttempts, missing value) to end of resultItems",
            "end if",
            "",
        ]
    )
    return lines


def _build_note_update_block(note_update: LocalSyncTaskNoteUpdate) -> list[str]:
    note_expression = _apple_script_text_expression(note_update.note)
    lines = [
        "set notesAttempts to 0",
        "set notesVerified to false",
        "set errorText to missing value",
        f"set expectedNoteText to my normalizeNewlines({note_expression})",
        "try",
        "    tell application \"Things3\"",
        f'        set taskRef to (to do id {_apple_script_string(note_update.task_id)})',
        "    end tell",
        f"    repeat with attemptIndex from 1 to {VERIFY_ATTEMPTS}",
        "        tell application \"Things3\"",
        "            set notes of taskRef to expectedNoteText",
        "            set actualNoteText to my normalizeNewlines(notes of taskRef as text)",
        "            if actualNoteText is expectedNoteText then",
        "                set notesAttempts to attemptIndex as integer",
        "                set notesVerified to true",
        "                exit repeat",
        "            end if",
        "        end tell",
        f"        if (attemptIndex as integer) < {VERIFY_ATTEMPTS} then delay {VERIFY_DELAY_SECONDS}",
        "    end repeat",
        "    if notesVerified is false then",
        f"        error \"Failed to verify note update after {VERIFY_ATTEMPTS} attempts.\"",
        "    end if",
        "on error errorMessage number errorNumber",
        "    set errorText to errorMessage & \" (\" & (errorNumber as text) & \")\"",
        "end try",
        "if errorText is not missing value then",
        f"    copy my buildNoteResultJson({_apple_script_string(note_update.task_id)}, {_apple_script_string(note_update.title)}, false, notesVerified, notesAttempts, errorText) to end of resultItems",
        "    set errorText to missing value",
        "else",
        f"    copy my buildNoteResultJson({_apple_script_string(note_update.task_id)}, {_apple_script_string(note_update.title)}, true, notesVerified, notesAttempts, missing value) to end of resultItems",
        "end if",
        "",
    ]
    return lines


def _parse_result_entry(entry: object) -> LocalSyncTaskMutationResult:
    if not isinstance(entry, dict):
        raise LocalSyncAppleScriptError("AppleScript task result entries must be JSON objects.")

    task_id = _require_output_text(entry.get("task_id"), field_name="task_id")
    title = _require_output_text(entry.get("title"), field_name="title")
    success = _require_output_bool(entry.get("success"), field_name="success")
    due_date_verified = _require_output_bool(
        entry.get("due_date_verified"),
        field_name="due_date_verified",
    )
    title_verified = _require_output_bool(
        entry.get("title_verified", False),
        field_name="title_verified",
    )
    project_verified = _require_output_bool(
        entry.get("project_verified"),
        field_name="project_verified",
    )
    trash_verified = _require_output_bool(
        entry.get("trash_verified"),
        field_name="trash_verified",
    )
    due_date_attempts = _require_output_attempts(
        entry.get("due_date_attempts"),
        field_name="due_date_attempts",
    )
    schedule_verified = _require_output_bool(
        entry.get("schedule_verified"),
        field_name="schedule_verified",
    )
    schedule_attempts = _require_output_attempts(
        entry.get("schedule_attempts"),
        field_name="schedule_attempts",
    )
    title_attempts = _require_output_attempts(
        entry.get("title_attempts", 0),
        field_name="title_attempts",
    )
    project_attempts = _require_output_attempts(
        entry.get("project_attempts"),
        field_name="project_attempts",
    )
    trash_attempts = _require_output_attempts(
        entry.get("trash_attempts"),
        field_name="trash_attempts",
    )

    error = entry.get("error")
    if error is not None and not isinstance(error, str):
        raise LocalSyncAppleScriptError("AppleScript task result field 'error' must be a string or null.")

    return LocalSyncTaskMutationResult(
        task_id=task_id,
        title=title,
        success=success,
        due_date_verified=due_date_verified,
        due_date_attempts=due_date_attempts,
        schedule_verified=schedule_verified,
        schedule_attempts=schedule_attempts,
        title_verified=title_verified,
        title_attempts=title_attempts,
        project_verified=project_verified,
        project_attempts=project_attempts,
        trash_verified=trash_verified,
        trash_attempts=trash_attempts,
        error=error,
    )


def _parse_note_update_result_entry(entry: object) -> LocalSyncTaskNoteUpdateResult:
    if not isinstance(entry, dict):
        raise LocalSyncAppleScriptError("AppleScript task result entries must be JSON objects.")

    task_id = _require_output_text(entry.get("task_id"), field_name="task_id")
    title = _require_output_text(entry.get("title"), field_name="title")
    success = _require_output_bool(entry.get("success"), field_name="success")
    notes_verified = _require_output_bool(entry.get("notes_verified"), field_name="notes_verified")
    notes_attempts = _require_output_attempts(entry.get("notes_attempts"), field_name="notes_attempts")

    error = entry.get("error")
    if error is not None and not isinstance(error, str):
        raise LocalSyncAppleScriptError("AppleScript task result field 'error' must be a string or null.")

    return LocalSyncTaskNoteUpdateResult(
        task_id=task_id,
        title=title,
        success=success,
        notes_verified=notes_verified,
        notes_attempts=notes_attempts,
        error=error,
    )


def _validate_result_sequence(
    results: Sequence[LocalSyncTaskMutationResult],
    expected_mutations: Sequence[LocalSyncTaskMutation],
) -> None:
    if len(results) != len(expected_mutations):
        raise LocalSyncAppleScriptError(
            f"AppleScript returned {len(results)} task results for {len(expected_mutations)} mutations."
        )

    expected_pairs = [(mutation.task_id, mutation.title) for mutation in expected_mutations]
    actual_pairs = [(result.task_id, result.title) for result in results]
    if actual_pairs != expected_pairs:
        raise LocalSyncAppleScriptError(
            "AppleScript task results did not match the requested mutation order."
        )


def _validate_note_update_result_sequence(
    results: Sequence[LocalSyncTaskNoteUpdateResult],
    expected_note_updates: Sequence[LocalSyncTaskNoteUpdate],
) -> None:
    if len(results) != len(expected_note_updates):
        raise LocalSyncAppleScriptError(
            f"AppleScript returned {len(results)} task results for {len(expected_note_updates)} note updates."
        )

    expected_pairs = [(note_update.task_id, note_update.title) for note_update in expected_note_updates]
    actual_pairs = [(result.task_id, result.title) for result in results]
    if actual_pairs != expected_pairs:
        raise LocalSyncAppleScriptError(
            "AppleScript task results did not match the requested mutation order."
        )


def _require_text(value: str | None, *, field_name: str) -> str:
    cleaned = _clean_optional_text(value, field_name=field_name)
    if cleaned is None:
        raise ValueError(f"{field_name} is required.")
    return cleaned


def _clean_optional_text(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string when provided.")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} cannot be blank.")
    return cleaned


def _require_script_text(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _apple_script_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _apple_script_text_expression(value: str) -> str:
    normalized_value = value.replace("\r\n", "\n").replace("\r", "\n")
    segments = normalized_value.split("\n")
    return " & linefeed & ".join(_apple_script_string(segment) for segment in segments)


def _apple_script_date_literal(value: date) -> str:
    return f'date {_apple_script_string(f"{value.month}/{value.day}/{value.year} 00:00:00")}'


def _require_output_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LocalSyncAppleScriptError(
            f"AppleScript task result field '{field_name}' must be a non-empty string."
        )
    return value


def _require_output_bool(value: object, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise LocalSyncAppleScriptError(
            f"AppleScript task result field '{field_name}' must be a boolean."
        )
    return value


def _require_output_attempts(value: object, *, field_name: str) -> int:
    if not isinstance(value, int) or value < 0 or value > VERIFY_ATTEMPTS:
        raise LocalSyncAppleScriptError(
            f"AppleScript task result field '{field_name}' must be an integer between 0 and {VERIFY_ATTEMPTS}."
        )
    return value


__all__ = [
    "AppleScriptExecutionResult",
    "LocalSyncAppleScriptError",
    "LocalSyncProjectTarget",
    "LocalSyncTaskNoteUpdate",
    "LocalSyncTaskNoteUpdateResult",
    "LocalSyncTaskMutation",
    "LocalSyncTaskMutationResult",
    "VERIFY_ATTEMPTS",
    "VERIFY_DELAY_SECONDS",
    "apply_task_note_updates",
    "apply_task_mutations",
    "build_apply_task_note_updates_script",
    "build_apply_task_mutations_script",
    "parse_task_note_update_results",
    "parse_task_mutation_results",
    "run_osascript",
]
