#!/usr/bin/env python3
"""Rewrite legacy Mail-to-Things notes into the canonical local-sync format."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from canvas_things.local_sync_applescript import (  # noqa: E402
    LocalSyncAppleScriptError,
    LocalSyncTaskNoteUpdate,
    apply_task_note_updates,
)
from canvas_things.local_sync_notes import parse_task_note  # noqa: E402
from canvas_things.local_sync_things_db import (  # noqa: E402
    LocalSyncThingsDBError,
    ThingsTaskRecord,
    discover_open_tasks,
)
from canvas_things.managed_notes import rewrite_legacy_mail_to_things_note  # noqa: E402


@dataclass(frozen=True)
class LocalSyncNoteBackfillCandidate:
    """One legacy Inbox task that can be rewritten into the canonical note contract."""

    task_id: str
    title: str
    existing_note: str
    rewritten_note: str
    due_text: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill legacy Mail-to-Things Inbox notes into the canonical "
            "Canvas-managed note format used by local sync."
        ),
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        help="Optional path to the Things SQLite database for testing or explicit selection.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write rewritten notes back into Things. Omit for dry-run preview.",
    )
    return parser


def collect_note_backfill_candidates(tasks: Sequence[ThingsTaskRecord]) -> tuple[LocalSyncNoteBackfillCandidate, ...]:
    """Return the legacy Inbox tasks that can be safely rewritten in place."""

    candidates: list[LocalSyncNoteBackfillCandidate] = []
    for task in tasks:
        existing_note = task.notes or ""
        parsed_existing = parse_task_note(existing_note)
        if parsed_existing.managed:
            continue

        rewritten_note = rewrite_legacy_mail_to_things_note(existing_note)
        if rewritten_note is None or rewritten_note == existing_note:
            continue

        parsed_rewritten = parse_task_note(rewritten_note)
        if not parsed_rewritten.writable or parsed_rewritten.due_text is None:
            continue

        candidates.append(
            LocalSyncNoteBackfillCandidate(
                task_id=task.uuid,
                title=task.title,
                existing_note=existing_note,
                rewritten_note=rewritten_note,
                due_text=parsed_rewritten.due_text,
            )
        )
    return tuple(candidates)


def run_backfill(*, db_path: Path | None = None, apply: bool = False) -> int:
    """Discover legacy Inbox notes, preview them, and optionally write the rewrite batch."""

    discovery = discover_open_tasks(None, db_path=db_path)
    candidates = collect_note_backfill_candidates(discovery.tasks)

    if not candidates:
        print("No legacy Inbox tasks require note backfill.")
        return 0

    mode = "apply" if apply else "dry-run"
    print(f"Found {len(candidates)} legacy Inbox task(s) eligible for note backfill ({mode}).")
    for candidate in candidates:
        print(f"- task_id={candidate.task_id} title={candidate.title!r} due={candidate.due_text}")

    if not apply:
        print("Re-run with --apply to rewrite those note bodies in Things.")
        return 0

    updates = tuple(
        LocalSyncTaskNoteUpdate(
            task_id=candidate.task_id,
            title=candidate.title,
            note=candidate.rewritten_note,
        )
        for candidate in candidates
    )
    results = apply_task_note_updates(updates)

    success_count = 0
    failure_count = 0
    for result in results:
        if result.success:
            success_count += 1
        else:
            failure_count += 1
        print(
            f"  update task_id={result.task_id} success={result.success} "
            f"attempts={result.notes_attempts} error={result.error or '-'}"
        )

    print(
        f"Backfill note update summary processed={len(results)} "
        f"successes={success_count} failures={failure_count}"
    )
    return 0 if failure_count == 0 else 1


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        return run_backfill(db_path=args.db_path, apply=args.apply)
    except (LocalSyncAppleScriptError, LocalSyncThingsDBError) as exc:
        print(f"Backfill failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
