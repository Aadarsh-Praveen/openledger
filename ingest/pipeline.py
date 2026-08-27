"""
C1.4 — ingestion loop.

Backfill: chunked by created_date (monthly windows, offsets stay small within each
window), ordered by created_date,unique_key — matches Phase 0's already-proven-fast,
already-proven-stable pattern. (NOT ordered by :updated_at — see docs/decisions.md,
"where/order on different fields times out server-side": a created_date $where
combined with an :updated_at $order times out server-side. :updated_at is still read
as a plain selected column on every row, cheap, for observability — but the initial
incremental watermark is seeded from the earliest window's started_at minus the
buffer, NOT from any observed :updated_at value; see the watermark-handoff
correction in docs/decisions.md for why the data-derived value is unsafe.)

Incremental: chunked AND ordered by :updated_at,unique_key (both the same field —
the fast, proven combination), with a >= comparison and a 48-hour lookback buffer
(never a strict >, per decisions.md requirement A), query-window boundaries anchored
to 03:00 UTC (requirement A's boundary-alignment note), and a created_date lower
bound to stay scoped to the backfill window (see the C1.9 scope-leak fix).

Per window: retry-with-backoff on non-200 (handled inside socrata_client.get), a
row-count assertion against $select=count(*) for the same bound, and a checkpoint
written only after the window's Iceberg write is fully committed.
"""

import logging
from datetime import datetime, timedelta, timezone

from ingest import bronze, checkpoint, raw_landing, schema, socrata_client
from ingest.config import (
    BACKFILL_START,
    BATCH_BOUNDARY_ANCHOR_UTC_HOUR,
    ORDER_TIEBREAKER,
    STALENESS_ALARM_THRESHOLD,
    WATERMARK_BUFFER_HOURS,
    WATERMARK_FIELD,
)

log = logging.getLogger("ingest.pipeline")


def _staleness_alarm_message(staleness):
    """C6.2/C6.6: one alarm, self-explaining. consecutive_short_circuits is a
    strict subset of consecutive_no_advance, so its value tells a human which
    failure mode they're looking at without a second threshold."""
    no_adv = staleness["consecutive_no_advance"]
    sc = staleness.get("consecutive_short_circuits", 0)
    if sc >= no_adv and sc > 0:
        mode = (
            f"all {sc} were no-op short-circuits — the query window has not changed "
            f"between runs (frozen watermark/boundary, or the source has genuinely "
            f"stopped publishing)"
        )
    elif sc == 0:
        mode = (
            "queries ran and returned rows on each, but none newer than the stored "
            "watermark (source republishing stale data, or a watermark-compare bug)"
        )
    else:
        mode = f"{sc} of them were short-circuits, the rest real fetches with nothing newer"
    return (
        f"STALENESS ALARM: watermark has not advanced in {no_adv} consecutive runs "
        f"(threshold={STALENESS_ALARM_THRESHOLD}); {mode}. See C6.2 in docs/decisions.md."
    )


def month_windows(start_iso, end_exclusive):
    """Yield (label, start_iso, end_exclusive_iso) monthly created_date windows."""
    start = datetime.fromisoformat(start_iso)
    end = end_exclusive
    cur = datetime(start.year, start.month, 1)
    while cur < end:
        nxt = datetime(cur.year + 1, 1, 1) if cur.month == 12 else datetime(cur.year, cur.month + 1, 1)
        win_start = max(cur, start)
        win_end = min(nxt, end)
        label = f"{cur.year:04d}-{cur.month:02d}"
        yield label, win_start.isoformat(timespec="milliseconds"), win_end.isoformat(timespec="milliseconds")
        cur = nxt


def _land_and_upsert(pages_iter, label, table):
    rows_fetched = 0
    rows_updated = 0
    rows_inserted = 0
    max_watermark_seen = None
    for page_idx, page in enumerate(pages_iter):
        raw_landing.land_page(page, label, page_idx)
        arrow_batch = schema.rows_to_arrow(page)
        result = bronze.scoped_upsert(table, arrow_batch)
        rows_updated += result["rows_updated"]
        rows_inserted += result["rows_inserted"]
        rows_fetched += len(page)
        for v in arrow_batch.column("updated_at").to_pylist():
            if v is not None and (max_watermark_seen is None or v > max_watermark_seen):
                max_watermark_seen = v
    return {
        "rows_fetched": rows_fetched,
        "rows_updated": rows_updated,
        "rows_inserted": rows_inserted,
        "rows_no_op": rows_fetched - rows_updated - rows_inserted,
        "max_watermark_seen": max_watermark_seen,
    }


def run_backfill_window(label, win_start, win_end, table):
    """One created_date-bounded backfill window: fetch, land, upsert, checkpoint."""
    if checkpoint.is_window_complete(label):
        log.info(f"Window {label} already complete, skipping.")
        return {"rows_fetched": 0, "rows_updated": 0, "rows_inserted": 0, "rows_no_op": 0, "max_watermark_seen": None}

    checkpoint.mark_window_started(label, win_start, win_end)

    where_clause = f"created_date >= '{win_start}' and created_date < '{win_end}'"
    order_clause = f"created_date,{ORDER_TIEBREAKER}"
    select_clause = schema.select_clause()

    expected_count = socrata_client.count(where_clause)
    pages = socrata_client.paginate(where_clause, order_clause, select_clause)
    result = _land_and_upsert(pages, label, table)

    count_matched = result["rows_fetched"] == expected_count
    if not count_matched:
        log.error(f"COUNT MISMATCH window={label}: fetched={result['rows_fetched']} expected={expected_count}")

    max_wm = result["max_watermark_seen"]
    checkpoint.mark_window_complete(
        label, result["rows_fetched"], expected_count,
        max_wm.isoformat() if max_wm else None,
        count_matched,
    )
    return result


def run_backfill(start_iso=BACKFILL_START, end_exclusive=None):
    if end_exclusive is None:
        end_exclusive = datetime.now(timezone.utc).replace(tzinfo=None)
    table = bronze.get_or_create_bronze_table()

    total_rows = 0
    windows = list(month_windows(start_iso, end_exclusive))
    log.info(f"Backfill: {len(windows)} monthly windows, {start_iso} to {end_exclusive.isoformat()}")

    for label, win_start, win_end in windows:
        window_start_time = datetime.now(timezone.utc)
        result = run_backfill_window(label, win_start, win_end, table)
        elapsed = (datetime.now(timezone.utc) - window_start_time).total_seconds()
        rows = result["rows_fetched"]
        total_rows += rows
        cp = checkpoint.load_checkpoint()
        w = cp["windows"].get(label, {})
        count_matched = w.get("count_matched")
        log.info(
            f"Window {label}: {rows} rows, count_matched={count_matched}, "
            f"elapsed={elapsed:.1f}s (running total {total_rows})"
        )

    # Watermark seeding (per Q1 review, docs/decisions.md): NOT the max :updated_at
    # observed in the data — that trends toward "now" and strands any row whose
    # created_date-window was already pulled *before* a concurrent-with-backfill
    # change happened elsewhere. Instead: the earliest window `started_at` across
    # every checkpointed window (this run's and any prior run's, e.g. the kill/
    # resume test's 2026-05/06), minus the 48h buffer. This is the earliest moment
    # at which any currently-committed window's data could have gone stale relative
    # to a live-source change, so it's the correct safety floor for a one-time
    # re-scan — MERGE absorbs the overlap at no cost (C1.3b/scoped_upsert no-op).
    cp = checkpoint.load_checkpoint()
    started_ats = [
        datetime.fromisoformat(w["started_at"])
        for w in cp["windows"].values()
        if w.get("status") == "complete" and "started_at" in w
    ]
    if started_ats:
        earliest_start = min(started_ats)
        seed_watermark = earliest_start - timedelta(hours=WATERMARK_BUFFER_HOURS)
        checkpoint.save_watermark(seed_watermark.isoformat())
        log.info(
            f"Backfill complete. Earliest window started_at: {earliest_start.isoformat()}. "
            f"Initial watermark seeded (earliest_start - {WATERMARK_BUFFER_HOURS}h buffer): "
            f"{seed_watermark.isoformat()}"
        )

    return total_rows


def _anchor_boundary(dt):
    """Round a UTC datetime down to the most recent BATCH_BOUNDARY_ANCHOR_UTC_HOUR
    boundary — avoids splitting a batch-stamp cluster (~01:33-02:03 UTC observed)
    across two incremental-run windows."""
    anchor = dt.replace(hour=BATCH_BOUNDARY_ANCHOR_UTC_HOUR, minute=0, second=0, microsecond=0)
    if anchor > dt:
        anchor -= timedelta(days=1)
    return anchor


def run_incremental():
    """A single incremental run: :updated_at >= (last watermark - buffer), up to
    the current 03:00-UTC-anchored boundary. Ordered and chunked on the same field
    (:updated_at) — the combination proven fast and stable."""
    table = bronze.get_or_create_bronze_table()

    stored_watermark = checkpoint.load_watermark()
    if stored_watermark is None:
        raise RuntimeError("No watermark found — run the backfill first.")

    # Normalize to naive UTC before formatting — stored_watermark may be offset-aware
    # (isoformat() on an aware datetime already includes "+00:00"), and appending a
    # literal "Z" on top of that produces a malformed "+00:00Z" suffix Socrata rejects.
    watermark_dt = datetime.fromisoformat(stored_watermark)
    if watermark_dt.tzinfo is not None:
        watermark_dt = watermark_dt.astimezone(timezone.utc).replace(tzinfo=None)
    query_start = watermark_dt - timedelta(hours=WATERMARK_BUFFER_HOURS)
    query_end = _anchor_boundary(datetime.now(timezone.utc).replace(tzinfo=None))

    # created_date lower-bound is required here: :updated_at advances on ANY row in
    # the full 22.2M-row source dataset that gets touched by a batch/republish
    # cycle, regardless of created_date. Without this bound, a republish event can
    # silently pull rows from outside the chosen 24-month backfill window into
    # bronze (caught in C1.9: a batch touch pulled in 4,340 rows with created_date
    # back to 2020 — see docs/decisions.md). This keeps the incremental pipeline
    # scoped to the same window the backfill (and the H0.3 decision) committed to.
    where_clause = (
        f":updated_at >= '{query_start.isoformat(timespec='milliseconds')}Z' and "
        f":updated_at < '{query_end.isoformat(timespec='milliseconds')}Z' and "
        f"created_date >= '{BACKFILL_START}'"
    )
    order_clause = f"{WATERMARK_FIELD},{ORDER_TIEBREAKER}"
    select_clause = schema.select_clause()

    # No-op short-circuit: if this run's window is IDENTICAL to the most recent
    # incremental run's window (both query_start and query_end unchanged), no
    # new publish cycle has occurred since — the watermark only advances on
    # genuine new activity, and query_end only advances once the 03:00 UTC
    # boundary is crossed, so a repeat window cannot contain new data. This is
    # NEVER silent: logged at WARNING with the exact repeated bounds, and
    # recorded as its own checkpoint entry (status=skipped_no_new_window) — a
    # quiet skip would be indistinguishable from a stalled pipeline, and with
    # a 2.53-92 day publish lag, staleness is exactly what can't be caught by
    # inspection otherwise. See docs/decisions.md for the alerting threshold
    # this feeds into for Phase 6.
    window_start_iso = query_start.isoformat()
    window_end_iso = query_end.isoformat()
    latest = checkpoint.get_latest_incremental_entry()
    if latest is not None and latest.get("start") == window_start_iso and latest.get("end") == window_end_iso:
        reason = (
            f"Window [{window_start_iso}, {window_end_iso}) is identical to the most recent "
            f"incremental run ({latest['label']}) — no new publish cycle since then."
        )
        log.warning(f"SHORT-CIRCUIT: skipping incremental run. {reason}")
        skip_label = f"incremental_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}"
        checkpoint.mark_window_skipped(skip_label, window_start_iso, window_end_iso, reason)
        staleness = checkpoint.record_run_outcome(advanced=False, short_circuited=True)
        if staleness["consecutive_no_advance"] >= STALENESS_ALARM_THRESHOLD:
            log.error(_staleness_alarm_message(staleness))
        return {
            "rows_fetched": 0, "rows_updated": 0, "rows_inserted": 0, "rows_no_op": 0,
            "max_watermark_seen": None, "skipped": True, "staleness": staleness,
        }

    expected_count = socrata_client.count(where_clause)
    # Unique per run (timestamp + microseconds) so same-day re-runs accumulate
    # distinct checkpoint records instead of overwriting each other's history.
    label = f"incremental_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}"
    checkpoint.mark_window_started(label, query_start.isoformat(), query_end.isoformat())

    pages = socrata_client.paginate(where_clause, order_clause, select_clause)
    result = _land_and_upsert(pages, label, table)
    max_watermark_seen = result["max_watermark_seen"]

    count_matched = result["rows_fetched"] == expected_count
    if not count_matched:
        log.error(f"COUNT MISMATCH incremental run: fetched={result['rows_fetched']} expected={expected_count}")

    checkpoint.mark_window_complete(
        label, result["rows_fetched"], expected_count,
        max_watermark_seen.isoformat() if max_watermark_seen else None,
        count_matched,
    )

    advanced = max_watermark_seen is not None and max_watermark_seen.replace(tzinfo=None) > watermark_dt.replace(tzinfo=None)
    if advanced:
        checkpoint.save_watermark(max_watermark_seen.isoformat())

    staleness = checkpoint.record_run_outcome(advanced=advanced, short_circuited=False)
    if staleness["consecutive_no_advance"] >= STALENESS_ALARM_THRESHOLD:
        log.error(_staleness_alarm_message(staleness))
    result["staleness"] = staleness

    log.info(
        f"Incremental run {label}: fetched={result['rows_fetched']} "
        f"updated={result['rows_updated']} inserted={result['rows_inserted']} "
        f"no_op={result['rows_no_op']} count_matched={count_matched}"
    )
    return result
