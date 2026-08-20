# Phase 1 — Incremental Ingestion → Iceberg Bronze

**Week:** 1 (back half)
**Estimated effort:** 6–9 hours
**Goal:** A watermarked, resumable, delta-only ingestion loop that lands NYC 311 into a
partitioned Iceberg bronze table, reconciled against a known row count.

## Carried forward from Phase 0

These are measured facts. Do not re-derive or contradict them.

| Fact | Value | Implication |
|---|---|---|
| Page-size cap | 50,000 rows | Backfill is 151 pages, not thousands |
| Backfill window | 24 months, locked | 2024-08-19 → present |
| Expected backfill rows | 7,511,072 | This is the reconciliation target |
| Median request latency | 0.426 s | Backfill request time ≈ 65 s |
| DuckDB | 1.5.5 (1.5+ line) | Partitioned UPDATE/DELETE available |
| Iceberg format version | 2, asserted | Required for Phase 7 Athena compatibility |
| DuckDB local reads | Need `unsafe_enable_version_guessing=true` | No version-hint file from PyIceberg's SQLite catalog |

---

## The design problem this phase must solve first

**A `created_date` watermark produces permanently stale rows.**

311 requests are created open and closed days or weeks later. If the incremental pull
filters on `created_date > watermark`, a row ingested while open is never revisited — its
`closed_date` stays null forever and `resolution_hours` is uncomputable. Agency SLA
analysis, the project's headline finding, would be built on a table that silently poisons
itself with every run.

The likely fix is to watermark on Socrata's `:updated_at` system field, which advances on
modification as well as insertion, and let the Iceberg MERGE absorb updates in place.
**Verify before building on it — C1.1 exists for exactly this.**

---

## HUMAN-ONLY tasks

### H1.1 — Approve the watermark strategy
C1.1 will report which watermark fields are actually available and queryable. Read that
report and confirm the chosen strategy before C1.4 is written. This is the single
architectural decision in Phase 1; do not delegate it.

### H1.2 — Be present for the first backfill run
Estimated at a few minutes of request time, but disk writes and MERGE will dominate.
Watch the first run rather than starting it and walking away — a wrong partition spec or
a schema surprise is much cheaper to catch at minute two than at completion.

---

## CLAUDE CODE tasks

### C1.1 — Verify watermark field availability (do this before writing any ingestion code)

Write `scripts/probe_watermark.py`. Answer each question with a live API call and report
the observed result. Do not infer any of this from knowledge of Socrata generally.

1. **Does `:updated_at` exist and is it selectable?** Request it explicitly in `$select`
   alongside `unique_key` and `created_date`. Print a sample.
2. **Is `:updated_at` filterable in `$where`?** Request a bounded `:updated_at` window and
   confirm the response is filtered, not ignored. A silently-ignored filter is the
   dangerous failure here — verify by comparing returned min/max against the requested
   bound, not by trusting HTTP 200.
3. **Is `:updated_at` orderable in `$order`?** Required for stable keyset pagination.
4. **Does `:updated_at` actually diverge from `created_date`?** Pull rows where
   `created_date` is in a window at least 60 days old, and measure what fraction have
   `:updated_at` materially later than `created_date`. **If that fraction is near zero,
   `:updated_at` is not tracking closures and the strategy fails** — report and stop.
5. **Are there other candidate fields?** Check for `:version`, `resolution_action_updated_date`,
   or any dataset-specific modification timestamp among the 44 columns.
6. **Quantify the staleness risk.** For requests created in a recent 30-day window, what
   fraction are still open at pull time? That number is the share of rows a `created_date`
   watermark would strand.

Write findings to `docs/source-notes.md` and a recommendation to `docs/decisions.md`.
**Then stop and report to the human (H1.1).** Do not proceed to C1.4 without approval.

### C1.2 — Resolve the column-count discrepancy
Phase 0 logged a 32-vs-44 column nuance. Determine the cause: sparse columns omitted from
JSON responses when null, a `$select` narrowing, or a genuine difference between the
portal's column list and the API's. Produce the **authoritative column list with types**
that the bronze schema will be built from, and record how it was obtained.

This matters because Iceberg schema evolution is cheap but schema *mistakes* are not —
a column typed wrong at creation propagates through every downstream model.

### C1.3 — Raw landing layer
Land each API page as Parquet under `raw/ingest_date=<YYYY-MM-DD>/`, preserving the API
payload with minimal transformation (type coercion only where JSON strings must become
timestamps). Raw is immutable and replayable; never edit a landed file.

Record bytes on disk and file count.

### C1.4 — Ingestion loop

**Chunk by date range, not by deep `$offset`.** With 7.5M rows a naive offset walk reaches
`$offset=7500000`, and deep offsets on Socrata degrade and can become unreliable. Instead:
partition the backfill into monthly (or weekly) windows, and paginate within each window
where offsets stay small. This also makes the run naturally resumable and checkpointable.

Requirements:
- **Stable total ordering** within each window: order by the watermark field, then
  `unique_key` as tiebreaker. Phase 0 proved this yields zero overlap — preserve it.
- **Checkpoint after each completed window** to a local state file: window bounds, rows
  fetched, watermark high value, completion status. A crash resumes at the next
  incomplete window, never from zero.
- **Watermark store**: a single durable record of the high-watermark. Advance it only
  after a window is fully committed to Iceberg, never after a partial write.
- **Retry with backoff** on non-200 responses. Log every retry.
- **Per-window row-count assertion**: compare rows fetched against a `$select=count(*)`
  for that same window. Log any mismatch loudly — a silent undercount is the worst
  possible failure mode here.

### C1.5 — Iceberg bronze table
- Format version **2**, explicitly asserted.
- Partitioned by **day on `created_date`** (stable — `created_date` never changes for a
  given `unique_key`, so rows never migrate partitions).
- Written via PyIceberg `upsert()` on `unique_key`.
- Directory layout must mirror the intended S3 prefix structure from Phase 0's
  documented tree, so Phase 7 is a copy-and-register rather than a rebuild.

**Scope the upsert.** A blind upsert against a 7.5M-row table per batch will be slow.
Since `created_date` determines the partition, restrict each upsert to the partitions the
incoming batch actually touches. Measure and record the difference — that's a real
optimization with a number attached.

### C1.6 — Run the backfill
Execute the full 24-month backfill. Record: wall-clock duration, rows landed, snapshot
count, warehouse size on disk, peak memory, and any retries.

**Reconcile against 7,511,072.** An exact match is unlikely — the source is live and rows
have been added since Phase 0's count. Report the delta and explain it. An unexplained
delta is a gate failure.

### C1.7 — Prove delta-only behavior
Run the pipeline a second time with no changes.

- Rows added must be only those genuinely new or modified since the first run.
- Total row count must not grow by a full re-ingest.
- Snapshot count must increment.
- Duration must be a small fraction of the backfill.

Then a third run immediately after the second: if the source hasn't changed, this should
add approximately zero rows. That's the cleanest proof the watermark works.

### C1.8 — Verify update absorption (the point of C1.1)
Find a `unique_key` whose row changed between runs — ideally one that went from open to
closed. Show the before and after values from the bronze table, and confirm the row count
did not increase. If no such row exists naturally in the run window, construct the proof
by re-ingesting an older window and showing MERGE updated rather than duplicated.

**This is the criterion that proves the pipeline is correct rather than merely running.**

### C1.9 — Re-run the DQ probe across the full backfill
Phase 0's baseline was a single July 2026 month. Re-run those six measures across the
full 24 months, broken out **by year**. The near-zero rates may be a 2026 artifact;
2020–2022 data may differ. Whatever the result, record it — this feeds Phase 3's scorecard
and determines whether the "defects are largely remediated" finding holds up.

### C1.10 — Journal and commit
`docs/decisions.md`: the watermark decision and its evidence, the date-chunking rationale,
the partition-scoped upsert measurement, and anything that behaved unexpectedly.
`docs/metrics.md`: every measured number from C1.6–C1.9.

One commit: `Phase 1: watermarked incremental ingestion to Iceberg bronze`.

---

## STOP GATE 1

Report each with the observed value.

| # | Criterion | Evidence required |
|---|---|---|
| 1 | Watermark field verified and approved | C1.1 findings; human approval recorded |
| 2 | Authoritative column list established | Column count with types, and how obtained |
| 3 | Backfill completed | Wall-clock, rows landed, snapshots, disk size |
| 4 | Reconciled against 7,511,072 | Delta stated and explained |
| 5 | Per-window count assertions passed | Zero unexplained mismatches across all windows |
| 6 | Checkpointing works | Kill mid-run and resume; show it restarted at the right window |
| 7 | Second run is delta-only | Rows added, duration, total count unchanged by re-ingest |
| 8 | Third run adds ≈ zero | Row delta |
| 9 | **Update absorption proven** | A specific `unique_key` before/after, row count unchanged |
| 10 | Partition-scoped upsert measured | Scoped vs unscoped duration |
| 11 | Time travel works on real data | Query an earlier snapshot, show row-count difference |
| 12 | DQ probe re-run by year | Six measures × N years in `docs/source-notes.md` |
| 13 | Journal and metrics updated | Real entries with numbers |
| 14 | One atomic commit | Message names Phase 1 |

**Load-bearing: 1, 5, 7, 9.** A failure in any of those is an architecture decision, not
a patch — report and stop.

Criterion 6 requires deliberately killing a run. Do it on purpose, early, on a small
window — not by hoping a crash happens.

---

## What Phase 2 will do (context only — do not start)

dbt project scaffolding, staging models over bronze, intermediate business logic, and the
Kimball star schema: `fct_service_requests` at request grain plus conformed dimensions for
agency, complaint type, location, and date.
