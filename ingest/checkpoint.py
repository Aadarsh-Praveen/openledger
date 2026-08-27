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
STALENESS_PATH = STATE_DIR / "staleness.json"


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
    """Most recent incremental-route checkpoint entry that actually FINISHED
    (complete or skipped), by its completed_at/skipped_at timestamp — used by
    the no-op short-circuit to compare against the last run's actual window
    bounds. Backfill's month-labeled ("YYYY-MM") entries are excluded.

    Bug found and fixed 2026-08-26 (C6.3 live S3 test, a genuine Socrata read
    timeout mid-run): an "in_progress" entry — a run that started but never
    completed or was recorded skipped — has no completed_at/skipped_at, but
    the old code fell back to started_at for it anyway, so a crashed run's
    entry was picked up as "the most recent run" and could false-positive the
    short-circuit on the very next attempt (same window bounds → treated as
    "no new data" even though the prior attempt never actually finished, and
    may have partially written to S3). Fixed by excluding any entry whose
    status isn't complete/skipped_no_new_window outright — matching what this
    docstring already claimed the function did. See docs/decisions.md, C6.3."""
    state = load_checkpoint()
    candidates = []
    for label, w in state["windows"].items():
        if not label.startswith("incremental_"):
            continue
        if w.get("status") not in ("complete", "skipped_no_new_window"):
            continue
        ts = w.get("completed_at") or w.get("skipped_at")
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


def load_staleness():
    if not STALENESS_PATH.exists():
        return {
            "consecutive_no_advance": 0,
            "consecutive_short_circuits": 0,
            "last_advanced": None,
            "last_short_circuited": None,
            "last_run_at": None,
        }
    with open(STALENESS_PATH) as f:
        return json.load(f)


def record_run_outcome(advanced: bool, short_circuited: bool = False):
    """C6.2/C6.6: track two nested consecutive-run counters, reset to 0 the
    moment the watermark advances. Kept in its own file, not watermark.json,
    so this doesn't disturb save_watermark's "write only on genuine advance"
    semantics.

    consecutive_no_advance     — runs where the watermark did not move, for
                                 ANY reason (a real fetch that returned
                                 nothing newer, OR a no-op short-circuit).
                                 This is the alarm signal (C6.2).
    consecutive_short_circuits  — runs that short-circuited: the query window
                                 was byte-identical to the previous run's, so
                                 no Socrata query was even issued. A strict
                                 subset of no_advance. Tracked separately as a
                                 DIAGNOSTIC (C6.6) so a tripped alarm is
                                 self-explaining: short_circuits == no_advance
                                 means "cron fires, window never changes"
                                 (frozen watermark/boundary or a dead source);
                                 short_circuits == 0 while no_advance climbs
                                 means "queries run and return rows, but none
                                 newer than the watermark". One alarm, two
                                 counters — a second independent threshold on
                                 the subset would answer the same question."""
    state = load_staleness()
    state["consecutive_no_advance"] = 0 if advanced else state.get("consecutive_no_advance", 0) + 1
    if advanced:
        state["consecutive_short_circuits"] = 0
    elif short_circuited:
        state["consecutive_short_circuits"] = state.get("consecutive_short_circuits", 0) + 1
    else:
        state["consecutive_short_circuits"] = 0
    state["last_advanced"] = advanced
    state["last_short_circuited"] = short_circuited
    state["last_run_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_write(STALENESS_PATH, state)
    return state
