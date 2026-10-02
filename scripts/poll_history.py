"""Select and validate poller history without GitHub's server-side status filter."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


def select_run(runs: list[dict]) -> int:
    successful = [
        run for run in runs
        if run["status"] == "completed" and run["conclusion"] == "success"
    ]
    if not successful:
        raise ValueError("No successful poll history found; refusing to start with empty history.")
    latest = max(successful, key=lambda run: datetime.fromisoformat(run["createdAt"].replace("Z", "+00:00")))
    return int(latest["databaseId"])


def validate_state(path: Path) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise ValueError("Recovered history is empty or is not an object.")
    notified = raw.get("notified", raw)
    if not isinstance(notified, dict):
        raise ValueError("Recovered notification history is not an object.")
    for key, updated_at in notified.items():
        parts = key.split(":", 2)
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
            raise ValueError("Recovered history contains an invalid assignment fingerprint.")
        if not isinstance(updated_at, str) or parts[2] != updated_at:
            raise ValueError("Recovered history contains an invalid assignment version.")
    pending = raw.get("pending", [])
    if not isinstance(pending, list):
        raise ValueError("Recovered pending history is not a list.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state_file", nargs="?", type=Path)
    args = parser.parse_args()
    try:
        if args.state_file is None:
            print(select_run(json.load(sys.stdin)))
        else:
            validate_state(args.state_file)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Cannot recover poll history: {exc} Imports stopped to prevent duplicates.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
