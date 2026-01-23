"""CLI entry point for the Canvas → Things Mail Bridge."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

import pytz

from . import canvas_client, config, notifier, state

logger = logging.getLogger(__name__)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Poll Canvas and send assignment reminders to Things")
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to config.yml (defaults to config/config.yml)",
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=50,
        help="Canvas page size when fetching assignments",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single poll cycle (default)",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def poll(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    settings = config.load_config(args.config)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger.info("Starting Canvas poll in %s (dry_run=%s)", settings.run.timezone, settings.run.dry_run)

    store = state.StateStore(settings.run.state_file)
    store.load()

    client = canvas_client.CanvasClient(settings)
    mailer = notifier.Notifier(settings)

    newly_sent: List[str] = []
    skipped_dry_run: List[str] = []
    failed_count = 0

    # Process pending assignments first (from previous rate-limited runs)
    pending = store.get_pending()
    if pending:
        logger.info("Processing %s pending assignments from previous run", len(pending))
        pending_filtered = _filter_assignments(pending, store, settings)
        if pending_filtered:
            logger.info("%s pending assignments after filtering for %s", len(pending_filtered), "all courses")
            result = mailer.notify(pending_filtered)
            
            if not settings.run.dry_run:
                # Mark successfully sent pending assignments
                for assignment in pending_filtered:
                    if assignment.fingerprint() in result.sent:
                        store.mark_notified(assignment.fingerprint(), assignment.updated_at)
                        store.remove_pending(assignment)
                    elif assignment in result.failed:
                        # Keep in pending for next run
                        failed_count += 1
            else:
                logger.info("Dry run enabled; not updating state for pending entries")
            
            newly_sent.extend(result.sent)
            skipped_dry_run.extend(result.skipped)
            failed_count += len(result.failed)
        else:
            # All pending were filtered out (past due or already notified), clear them
            if not settings.run.dry_run:
                store.clear_pending()

    # Process new assignments from Canvas
    for course in settings.canvas.courses:
        logger.info("Fetching assignments for %s (%s)", course.alias, course.course_id)
        assignments = client.fetch_assignments(course, per_page=args.per_page)
        to_send = _filter_assignments(assignments, store, settings)
        logger.info("%s assignments after filtering for %s", len(to_send), course.alias)
        
        if not to_send:
            continue
            
        result = mailer.notify(to_send)

        if not settings.run.dry_run:
            # Only mark successfully sent assignments
            successfully_sent = [
                assignment for assignment in to_send
                if assignment.fingerprint() in result.sent
            ]
            store.bulk_mark((assignment.fingerprint(), assignment.updated_at) for assignment in successfully_sent)
            
            # Add failed assignments to pending queue
            for assignment in result.failed:
                store.add_pending(assignment)
                failed_count += 1
        else:
            logger.info("Dry run enabled; not updating state for %s entries", len(to_send))

        newly_sent.extend(result.sent)
        skipped_dry_run.extend(result.skipped)

    store.save()
    logger.info("Done. Sent %s assignments (%s dry-run, %s failed/pending)", len(newly_sent), len(skipped_dry_run), failed_count)
    return 0


def _filter_assignments(
    assignments: List[canvas_client.Assignment],
    store: state.StateStore,
    settings: config.Settings,
) -> List[canvas_client.Assignment]:
    filtered: List[canvas_client.Assignment] = []
    tz = pytz.timezone(settings.run.timezone)
    now = datetime.now(tz)
    
    for assignment in assignments:
        key = assignment.fingerprint()
        
        # Skip if already notified
        if not store.should_notify(key, assignment.updated_at):
            logger.debug("Skipping already notified assignment %s", key)
            continue
        
        # Filter out past-due assignments
        if assignment.due_at:
            try:
                # Parse Canvas ISO 8601 date (e.g., "2026-01-15T23:59:59Z")
                due_date = datetime.fromisoformat(assignment.due_at.replace("Z", "+00:00"))
                # Convert to configured timezone for comparison
                if due_date.tzinfo is None:
                    due_date = pytz.UTC.localize(due_date)
                due_date = due_date.astimezone(tz)
                
                if due_date < now:
                    logger.debug("Skipping past-due assignment %s (due: %s)", key, assignment.due_at)
                    continue
            except (ValueError, AttributeError) as exc:
                logger.warning("Failed to parse due date for assignment %s: %s (including anyway)", key, exc)
        
        filtered.append(assignment)
    
    return filtered


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(poll())
