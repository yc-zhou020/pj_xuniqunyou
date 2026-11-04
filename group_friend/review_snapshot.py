"""Saved review inputs tied to an explicit local binding, never platform IDs."""
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .douyin import ObservationError, capture, parse_window, select_window


def bound_messages(report, binding):
    window = select_window(report, binding.window_handle)
    if (window.get("process_id") != binding.process_id
            or window.get("process_started_at") != binding.process_started_at):
        raise ObservationError("client_restarted_rebind_required")
    return window, parse_window(window, expected_title=binding.window_title,
                                expected_heading=binding.group_heading)


def save_snapshot(path, binding, capture_fn=None):
    report = (capture_fn or capture)(binding.process_id)
    window, messages = bound_messages(report, binding)
    document = dict(review_snapshot=1, binding=asdict(binding),
                    captured_at=datetime.now(timezone.utc).isoformat(),
                    report=dict(schema=1, text_included=True, windows=[window]))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Do not silently replace the snapshot whose indices the user reviewed.
    with path.open("x", encoding="utf-8") as file:
        json.dump(document, file, ensure_ascii=False, indent=2)
    return messages


def load_snapshot(path, binding):
    raw = Path(path).read_bytes()
    document = json.loads(raw.decode("utf-8-sig"))
    if document.get("review_snapshot") != 1:
        raise ObservationError("review_requires_session_snapshot")
    if document.get("binding") != asdict(binding):
        raise ObservationError("snapshot_session_changed_recapture_required")
    _, messages = bound_messages(document["report"], binding)
    return raw, messages
