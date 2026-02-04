from __future__ import annotations

from pathlib import Path
from typing import List

from canvas_things import config
from canvas_things.canvas_client import Assignment
from canvas_things import state as st
from canvas_things import notifier as nt

def make_assignment(id: int, updated_at: str, title: str = "Test Assignment") -> Assignment:
    return Assignment(
        course_id=1,
        course_alias="TEST",
        assignment_id=id,
        title=title,
        html_url="http://example.com",
        updated_at=updated_at,
        due_at=None,
        lock_at=None,
        unlock_at=None,
        description=None,
        points_possible=10,
        submission_types=[],
        published=True,
    )

def test_state_tracks_known_assignments(tmp_path: Path):
    store = st.StateStore(tmp_path / "state.json")
    
    # New assignment
    a1 = make_assignment(101, "2025-01-01T10:00:00Z")
    
    # Not known initially
    assert not store.is_known_assignment(a1.course_id, a1.assignment_id)
    
    # Mark as notified
    store.mark_notified(a1.fingerprint(), a1.updated_at)
    
    # Should be known now
    assert store.is_known_assignment(a1.course_id, a1.assignment_id)
    
    # Verify index persistence
    store.save()
    store2 = st.StateStore(tmp_path / "state.json")
    store2.load()
    assert store2.is_known_assignment(a1.course_id, a1.assignment_id)

def test_update_flag_logic(tmp_path: Path):
    store = st.StateStore(tmp_path / "state.json")
    
    # 1. First version of assignment
    a1 = make_assignment(101, "2025-01-01T10:00:00Z")
    
    # Logic simulation from main.py
    is_update = False
    if store.is_known_assignment(a1.course_id, a1.assignment_id):
        is_update = True
    store.mark_notified(a1.fingerprint(), a1.updated_at)
    
    assert is_update is False
    
    # 2. Update to same assignment
    a1_v2 = make_assignment(101, "2025-01-02T12:00:00Z")
    
    # Logic simulation
    should_notify = store.should_notify(a1_v2.fingerprint(), a1_v2.updated_at)
    assert should_notify is True
    
    is_update_2 = False
    if store.is_known_assignment(a1_v2.course_id, a1_v2.assignment_id):
        is_update_2 = True
    
    assert is_update_2 is True

def test_notifier_adds_update_prefix():
    settings = config.Settings(
        canvas=config.CanvasConfig(base_url="", courses=[]),
        email=config.EmailConfig(
            subject_template="{title}",
            from_name="bot",
            include_description=True,
            max_description_chars=100
        ),
        run=config.RunConfig(timezone="UTC", state_file=Path("state.json"), dry_run=False),
        smtp_host="localhost", smtp_port=25, smtp_user="u", smtp_pass="p", things_email="t",
        canvas_token="mock_token"
    )
    
    # Normal assignment
    a1 = make_assignment(101, "2025-01-01")
    n = nt.Notifier(settings)
    msg1 = n._build_message(a1)
    assert "[UPDATE]" not in msg1["Subject"]
    assert "** UPDATE **" not in msg1.get_content()
    
    # Updated assignment
    a2 = make_assignment(101, "2025-01-02")
    a2.is_update_notification = True
    msg2 = n._build_message(a2)
    assert "[UPDATE] Test Assignment" in msg2["Subject"]
    assert "** UPDATE **" in msg2.get_content()
