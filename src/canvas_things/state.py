"""State persistence for Canvas → Things Mail Bridge."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable


class StateStore:
    """JSON-backed storage of assignment fingerprints."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: Dict[str, str] = {}

    def load(self) -> None:
        if not self.path.exists():
            self._data = {}
            return
        with self.path.open("r", encoding="utf-8") as fh:
            try:
                raw = json.load(fh)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"State file {self.path} is corrupted: {exc}") from exc
        if not isinstance(raw, dict):
            raise RuntimeError(f"State file {self.path} must contain an object.")
        self._data = {str(k): str(v) for k, v in raw.items()}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2, sort_keys=True)

    def should_notify(self, key: str, updated_at: str) -> bool:
        previous = self._data.get(key)
        return previous is None or updated_at > previous

    def mark_notified(self, key: str, updated_at: str) -> None:
        self._data[key] = updated_at

    def bulk_mark(self, entries: Iterable[tuple[str, str]]) -> None:
        for key, updated_at in entries:
            self.mark_notified(key, updated_at)

    def snapshot(self) -> Dict[str, str]:
        return dict(self._data)
