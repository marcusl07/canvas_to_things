
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from canvas_things.state import StateStore

def test_migration_and_deduplication():
    # Setup: Create a state file with fingerprint keys (ID+Timestamp)
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
        # Action: Load state; this keeps fingerprint keys and rebuilds the ID index.
        store = StateStore(tmp_path)
        store.load()

        snapshot = store.snapshot()
        
        # Fingerprint keys are preserved for exact-version notification checks.
        assert snapshot["101:202:2023-01-01T12:00:00Z"] == "2023-01-01T12:00:00Z"
        assert snapshot["101:204:2023-01-03T10:00:00Z"] == "2023-01-03T10:00:00Z"
        assert snapshot["101:204:2023-01-03T11:00:00Z"] == "2023-01-03T11:00:00Z"

        # The secondary index still knows assignments by course ID + assignment ID.
        assert store.is_known_assignment(101, 202) is True
        assert store.is_known_assignment(101, 204) is True

        # Same timestamp: Should be False (already notified)
        assert store.should_notify("101:202:2023-01-01T12:00:00Z", "2023-01-01T12:00:00Z") is False
        
        # Newer timestamp: Should be True
        assert store.should_notify("101:202:2023-01-01T12:00:01Z", "2023-01-01T12:00:01Z") is True

    finally:
        tmp_path.unlink()

if __name__ == "__main__":
    test_migration_and_deduplication()
    print("Test passed!")
