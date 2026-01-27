
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from canvas_things.state import StateStore
from canvas_things.canvas_client import Assignment

def test_migration_and_deduplication():
    # Setup: Create a state file with "old-style" keys (ID+Timestamp)
    old_state = {
        "notified": {
            # unique_id:updated_at key -> updated_at value
            "101:202:2023-01-01T12:00:00Z": "2023-01-01T12:00:00Z",
            "101:203:2023-01-02T12:00:00Z": "2023-01-02T12:00:00Z",
            # Simulate a "duplicate" entry for 204 that might exist due to older versions
            "101:204:2023-01-03T10:00:00Z": "2023-01-03T10:00:00Z",
            "101:204:2023-01-03T11:00:00Z": "2023-01-03T11:00:00Z"
        },
        "pending": [],
        "email_count": 0
    }

    with NamedTemporaryFile(mode="w", delete=False) as tmp:
        json.dump(old_state, tmp)
        tmp_path = Path(tmp.name)

    try:
        # Action: Load state with the new class (will trigger migration logic)
        store = StateStore(tmp_path)
        store.load()

        # Assertion 1: Check if keys are migrated to simple "course:assignment" format
        snapshot = store.snapshot()
        print("Snapshot:", snapshot)
        
        # We expect "101:202" -> "2023-01-01T12:00:00Z"
        assert "101:202" in snapshot
        assert snapshot["101:202"] == "2023-01-01T12:00:00Z"

        # Assertion 2: Check if duplicates for 204 resolved to the latest timestamp
        assert "101:204" in snapshot
        assert snapshot["101:204"] == "2023-01-03T11:00:00Z"

        # Assertion 3: Verify should_notify behavior
        # Same timestamp: Should be False (already notified)
        assert store.should_notify("101:202", "2023-01-01T12:00:00Z") is False
        
        # Newer timestamp: Should be True
        assert store.should_notify("101:202", "2023-01-01T12:00:01Z") is True
        
        # Older timestamp: Should be False
        assert store.should_notify("101:202", "2023-01-01T11:00:00Z") is False

    finally:
        tmp_path.unlink()

if __name__ == "__main__":
    test_migration_and_deduplication()
    print("Test passed!")
