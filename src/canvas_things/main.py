"""CLI entry point for the Canvas → Things Mail Bridge."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Iterable, List

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

    for course in settings.canvas.courses:
        logger.info("Fetching assignments for %s (%s)", course.alias, course.course_id)
        assignments = client.fetch_assignments(course, per_page=args.per_page)
        to_send = _filter_assignments(assignments, store)
        logger.info("%s assignments after dedupe for %s", len(to_send), course.alias)
        result = mailer.notify(to_send)

        if not settings.run.dry_run:
            store.bulk_mark((assignment.fingerprint(), assignment.updated_at) for assignment in to_send)
        else:
            logger.info("Dry run enabled; not updating state for %s entries", len(to_send))

        newly_sent.extend(result.sent)
        skipped_dry_run.extend(result.skipped)

    store.save()
    logger.info("Done. Sent %s assignments (%s dry-run)", len(newly_sent), len(skipped_dry_run))
    return 0


def _filter_assignments(assignments: List[canvas_client.Assignment], store: state.StateStore) -> List[canvas_client.Assignment]:
    filtered: List[canvas_client.Assignment] = []
    for assignment in assignments:
        key = assignment.fingerprint()
        if store.should_notify(key, assignment.updated_at):
            filtered.append(assignment)
        else:
            logger.debug("Skipping already notified assignment %s", key)
    return filtered


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(poll())
