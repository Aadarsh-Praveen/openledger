"""
Checkpoint and watermark state, per C1.4:
- Checkpoint after each completed window: bounds, rows fetched, watermark high
  value, completion status. A crash resumes at the next incomplete window.
- Watermark store: a single durable high-watermark record, advanced only after a
  window is fully committed to Iceberg, never after a partial write.

Plain JSON, written atomically (write to a temp file, then os.replace) so a crash
mid-write can't corrupt the existing checkpoint.
"""

import json
import os
import tempfile
from datetime import datetime, timezone

from ingest.config import STATE_DIR

CHECKPOINT_PATH = STATE_DIR / "backfill_checkpoint.json"
WATERMARK_PATH = STATE_DIR / "watermark.json"


def _atomic_write(path, data):
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def load_checkpoint():
    if not CHECKPOINT_PATH.exists():
        return {"windows": {}}
    with open(CHECKPOINT_PATH) as f:
        return json.load(f)


def save_checkpoint(state):
    _atomic_write(CHECKPOINT_PATH, state)


def is_window_complete(window_label):
    state = load_checkpoint()
    w = state["windows"].get(window_label)
    return bool(w and w.get("status") == "complete")


def mark_window_started(window_label, start, end):
    state = load_checkpoint()
    state["windows"][window_label] = {
        "start": start,
        "end": end,
        "status": "in_progress",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    save_checkpoint(state)


def mark_window_complete(window_label, rows_fetched, expected_count, watermark_high, count_matched):
    state = load_checkpoint()
    w = state["windows"].setdefault(window_label, {})
    w.update({
        "status": "complete",
        "rows_fetched": rows_fetched,
        "expected_count": expected_count,
        "count_matched": count_matched,
        "watermark_high": watermark_high,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })
    save_checkpoint(state)


def load_watermark():
    if not WATERMARK_PATH.exists():
        return None
    with open(WATERMARK_PATH) as f:
        return json.load(f).get("watermark")


def save_watermark(value):
    _atomic_write(WATERMARK_PATH, {
        "watermark": value,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
