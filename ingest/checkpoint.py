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


def mark_window_skipped(window_label, window_start, window_end, reason):
    """Record a short-circuited run — never silent. A skipped run still gets a
    full checkpoint entry (status=skipped_no_new_window) so run history shows
    it happened, distinct from a run that genuinely fetched and found nothing."""
    state = load_checkpoint()
    state["windows"][window_label] = {
        "start": window_start,
        "end": window_end,
        "status": "skipped_no_new_window",
        "reason": reason,
        "skipped_at": datetime.now(timezone.utc).isoformat(),
    }
    save_checkpoint(state)


def get_latest_incremental_entry():
    """Most recent incremental-route checkpoint entry (complete or skipped),
    by its started_at/skipped_at timestamp — used by the no-op short-circuit
    to compare against the last run's actual window bounds. Backfill's
    month-labeled ("YYYY-MM") entries are excluded."""
    state = load_checkpoint()
    candidates = []
    for label, w in state["windows"].items():
        if not label.startswith("incremental_"):
            continue
        ts = w.get("completed_at") or w.get("skipped_at") or w.get("started_at")
        if ts:
            candidates.append((ts, label, w))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    _, label, w = candidates[-1]
    return {"label": label, **w}


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
