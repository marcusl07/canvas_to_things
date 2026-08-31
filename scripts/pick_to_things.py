#!/usr/bin/env python3
"""On-demand Canvas → Things 3 import tool.

Fetches assignments for configured courses, lets you pick specific ones by
number, and opens things:///add URLs to create Things tasks with the Canvas
due date set as the actual Things Deadline field.

Requires only CANVAS_TOKEN in the environment — no SMTP or Things-email vars.

Usage:
    CANVAS_TOKEN=xxx python3 scripts/pick_to_things.py --config config/config.yml
    CANVAS_TOKEN=xxx python3 scripts/pick_to_things.py --days 14 --dry-run
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

import yaml

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from canvas_things.canvas_client import Assignment, CanvasClient  # noqa: E402
from canvas_things.config import CanvasConfig, CourseConfig  # noqa: E402


# ---------------------------------------------------------------------------
# Narrow config loader — requires only CANVAS_TOKEN, not SMTP/Things-email
# ---------------------------------------------------------------------------

def _load_pick_config(path: Path) -> tuple[CanvasConfig, str, str]:
    """Load canvas + timezone config from YAML and CANVAS_TOKEN from env.

    Returns (canvas_config, timezone_name, canvas_token).
    Raises SystemExit on missing required values.
    """
    if not path.exists():
        sys.exit(
            f"Config file not found: {path}\n"
            "Copy config/config.example.yml to config/config.yml and fill in your settings."
        )

    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    canvas_token = os.environ.get("CANVAS_TOKEN", "").strip()
    if not canvas_token:
        sys.exit("CANVAS_TOKEN environment variable is required.")

    canvas_data = data.get("canvas") or {}
    run_data = data.get("run") or {}

    # Resolve base_url: support ${ENV_VAR} placeholders used in config.example.yml
    base_url: str = canvas_data.get("base_url") or ""
    if base_url.startswith("${") and base_url.endswith("}"):
        env_key = base_url[2:-1]
        base_url = os.environ.get(env_key, "")
    if not base_url:
        base_url = os.environ.get("CANVAS_BASE_URL", "").strip()
    if not base_url:
        sys.exit("canvas.base_url is required in config.yml or via CANVAS_BASE_URL env var.")

    raw_courses = canvas_data.get("courses") or []
    if not raw_courses:
        sys.exit("At least one course must be defined under canvas.courses in config.yml.")

    courses: List[CourseConfig] = []
    for entry in raw_courses:
        try:
            course_id = int(entry["id"])
        except (KeyError, ValueError, TypeError):
            sys.exit("Each course entry must have an integer 'id'.")
        alias = entry.get("alias") or str(course_id)
        include_desc = bool(entry.get("include_description", True))
        courses.append(CourseConfig(course_id=course_id, alias=alias, include_description=include_desc))

    timezone_name: str = run_data.get("timezone") or "UTC"

    return CanvasConfig(base_url=base_url.rstrip("/"), courses=courses), timezone_name, canvas_token


# ---------------------------------------------------------------------------
# Pure logic (also the seam for unit tests)
# ---------------------------------------------------------------------------

def filter_assignments(
    assignments: List[Assignment],
    tz: ZoneInfo,
    days: int,
    include_undated: bool,
    now: Optional[datetime] = None,
) -> List[Assignment]:
    """Return assignments whose due date falls within [now, now+days] in local time.

    Undated assignments are included only when include_undated is True.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days)
    result: List[Assignment] = []
    for a in assignments:
        if a.due_at is None:
            if include_undated:
                result.append(a)
        else:
            dt_utc = datetime.fromisoformat(a.due_at.replace("Z", "+00:00"))
            if now <= dt_utc <= cutoff:
                result.append(a)
    return result


def due_date_local(due_at_utc: str, tz: ZoneInfo) -> str:
    """Convert a Canvas UTC due_at string to a local YYYY-MM-DD string for Things."""
    dt_utc = datetime.fromisoformat(due_at_utc.replace("Z", "+00:00"))
    return dt_utc.astimezone(tz).strftime("%Y-%m-%d")


def build_things_url(title: str, notes: str, deadline: Optional[str]) -> str:
    """Construct a things:///add URL with percent-encoded parameters.

    deadline must be YYYY-MM-DD or None (omitted when None).
    """
    params = f"title={quote(title)}&notes={quote(notes)}"
    if deadline:
        params += f"&deadline={deadline}"
    return f"things:///add?{params}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pick Canvas assignments and add them directly to Things 3 with the deadline set."
    )
    parser.add_argument(
        "--config",
        default="config/config.yml",
        metavar="PATH",
        help="Path to config YAML (default: config/config.yml)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=21,
        metavar="N",
        help="Show assignments due within the next N days (default: 21)",
    )
    parser.add_argument(
        "--course",
        metavar="ALIAS",
        help="Restrict to one course alias (case-insensitive)",
    )
    parser.add_argument(
        "--include-undated",
        action="store_true",
        help="Also show assignments with no due date",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print things:///add URLs instead of opening them",
    )
    args = parser.parse_args()

    canvas_config, tz_name, canvas_token = _load_pick_config(Path(args.config))
    tz = ZoneInfo(tz_name)

    # Duck-typed minimal settings — CanvasClient only reads .canvas and .canvas_token
    settings_like = SimpleNamespace(canvas=canvas_config, canvas_token=canvas_token)
    client = CanvasClient(settings_like)  # type: ignore[arg-type]

    all_assignments: List[Assignment] = []
    for course in canvas_config.courses:
        if args.course and course.alias.lower() != args.course.lower():
            continue
        print(f"Fetching assignments for {course.alias}...")
        all_assignments.extend(client.fetch_assignments(course))

    candidates = filter_assignments(
        all_assignments,
        tz=tz,
        days=args.days,
        include_undated=args.include_undated,
    )

    if not candidates:
        print("No assignments found in the specified window.")
        return

    print(f"\nFound {len(candidates)} assignment(s):\n")
    for i, a in enumerate(candidates, 1):
        if a.due_at:
            date_str = due_date_local(a.due_at, tz)
            due_display = f"due {date_str}"
        else:
            due_display = "no due date"
        print(f"  {i:>2}. [{a.course_alias}] {a.title} ({due_display})")

    print()
    try:
        choice = input("Pick assignments (e.g. 1,3,5 or 'a' for all, Enter to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return

    if not choice:
        print("No selection made.")
        return

    if choice.lower() == "a":
        selected = candidates
    else:
        try:
            indices = [int(x.strip()) for x in choice.split(",")]
        except ValueError:
            print("Invalid input — use numbers separated by commas or 'a' for all.", file=sys.stderr)
            sys.exit(1)
        selected = []
        for idx in indices:
            if idx < 1 or idx > len(candidates):
                print(f"Index {idx} out of range (1–{len(candidates)}).", file=sys.stderr)
                sys.exit(1)
            selected.append(candidates[idx - 1])

    print()
    for a in selected:
        title = f"[{a.course_alias}] {a.title}"
        notes = a.html_url or ""
        deadline = due_date_local(a.due_at, tz) if a.due_at else None
        url = build_things_url(title, notes, deadline)
        if args.dry_run:
            print(f"[DRY RUN] {url}")
        else:
            subprocess.run(["open", url], check=True)
            print(f"Added: {title}")


if __name__ == "__main__":
    main()
