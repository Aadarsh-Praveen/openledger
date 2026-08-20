# Metrics

Measured numbers only. Never estimate or backfill a number that wasn't measured.

## Phase 0

All measured live on 2026-08-19 against `erm2-nwe9`. Source: `scripts/verify_source.py`
and `scripts/probe_data_quality.py`.

- **Backfill window row count (24 months, 2024-08-19 to present):** 7,511,072 rows
- **Observed Socrata page-size cap:** 50,000 rows/page
- **Median request latency (20 sequential requests):** 0.426s
- **Estimated pure-request-time for full backfill at observed cap/latency:** ~65s (0.02h)
- **DQ baseline sample:** 342,892 rows (July 2026, full month)
  - closed_date < created_date: 0.0190%
  - status=Closed with null closed_date: 0.0003%
  - missing latitude/longitude: 1.9747%
  - (0,0) or out-of-NYC-bounds coordinates: 0.0000%
  - distinct complaint_type: 176; distinct agency: 14
  - borough Unspecified/null: 0.1342%
- **Iceberg smoke test:** snapshot count 0→1→2 across 2 appends; upsert left row
  count unchanged (5→5) while updating the collided row's value; time-travel read
  against snapshot 1 correctly returned 3 pre-second-batch rows; DuckDB read via
  `iceberg_scan` matched PyIceberg's row count (5 = 5)

## Phase 1

**C1.5 — partition-scoped upsert, measured against the real full-scale table (7.5M
rows, ~730 partitions):** unscoped `table.upsert()` **3.37s** vs. scoped
`bronze.scoped_upsert()` **0.09s** on an identical 500-row no-op batch —
**38.8x speedup**. See `docs/decisions.md` for method and caveats (benefit is
largest for `created_date`-chunked batches; weaker for `:updated_at`-chunked ones).

**C1.6 — full 24-month backfill (2024-08-19 to 2026-08-20), run live 2026-08-20:**
- Total rows landed: **7,522,072** (across 25 monthly windows, 2024-08 partial
  through 2026-08 partial)
- Reconciliation against Phase 0's 7,511,072 baseline: **+11,000 delta**, explained
  by one day of live source growth between the Phase 0 count (2026-08-19) and this
  backfill (2026-08-20) at the measured ~11,905/day steady-state rate — not
  unexplained
- Wall-clock duration: **~53 minutes** (00:00:30–00:53:07 UTC), faster than the
  75-90 minute estimate from the 2-month test extrapolation
- Snapshot count: **163**
- Warehouse size on disk: **731 MB**, 869 Parquet data files
- Per-window count-assertion: **25/25 windows matched** `$select=count(*)`, zero
  mismatches
- Peak memory: not instrumented in this run (not captured — would need to be added
  to `ingest/pipeline.py` if this becomes a recurring measurement)
- Retries: zero non-200 responses encountered during the real backfill (the
  same-day earlier mixed-`$where`/`$order` probe did trigger retries, but that was
  a diagnostic query, not part of the backfill itself)
- Criterion 6 (kill/resume): proven on real data mid-run (2026-05/06 windows) — see
  `docs/decisions.md`; zero duplication after resume, checkpoint correctly skipped
  the completed window and restarted the in-progress one
- Initial incremental watermark seeded: `2026-08-18T01:22:36.364129+00:00`
  (earliest window `started_at` across all checkpointed windows, minus the 48h
  buffer — see `docs/decisions.md`'s watermark-handoff correction)

**C1.7 — delta-only proof, three incremental runs, run live 2026-08-20:**

Run 2 surfaced a real bug (see `docs/decisions.md`, "Bug found via C1.9") — its
"+4,340 net rows" turned out to be entirely an incremental-query scope leak (no
`created_date` bound), not genuine new activity. Fixed, purged, and re-verified;
Run 4 below (post-fix) is the authoritative measurement.

| | Run 2 (pre-fix) | Run 3 (pre-fix, immediately after) | Run 4 (post-fix) |
|---|---|---|---|
| Rows fetched | 589,388 | 550,567 | 546,445 |
| Rows updated | not captured (instrumentation added after this run) | 0 | **0** |
| Rows inserted | 4,340 (= exact row-count delta; **later found to be 100% out-of-scope stray rows, purged**) | 0 | **0** |
| Rows no-op | not captured separately | 550,567 (100.00%) | **546,445 (100.00%)** |
| Total row count before → after | 7,522,072 → 7,526,412 | 7,526,412 → 7,526,412 | **7,522,072 → 7,522,072 (unchanged)** |
| Snapshot count before → after | 163 → 169 (+6) | 169 → 169 (+0) | **170 → 170 (+0)** |
| Duration | 1008.5s | 1039.5s | 922.9s |

All three runs' large fetch volume (not "small" in the naive sense) is explained by
the 48h lookback buffer overlapping the known Aug-19 republish spike — recorded as
a monitored metric per the agreed 7c redefinition, not a gate failure. **C1.7d**
(no-op measurement, authoritative run 4): **100.00%** of fetched rows
(546,445/546,445) had zero material field change and zero row/snapshot growth —
clean proof of both delta-only behavior (criterion 7) and near-zero third-run
growth (criterion 8), on a correctly-scoped table.

**C1.8 — update absorption proof (real data, via Iceberg time travel):**
- `unique_key=68857791`: pre-incremental snapshot (7,522,072 rows) shows
  `status=Open, closed_date=None, updated_at=2026-05-15T01:36:13.979Z`; post-run-2
  snapshot shows `status=Closed, closed_date=2026-08-18T19:03:01,
  updated_at=2026-08-20T01:33:14.813Z`. Confirmed in-scope (`created_date=
  2026-05-02`), unaffected by the scope-leak bug/purge.
- Row count for this `unique_key`: **exactly 1** in both the pre-incremental and
  post-update snapshot — proves genuine in-place MERGE update, not a duplicate
  insert.
- Doubles as **criterion 11** (time travel with a real row-count difference):
  7,522,072 (earlier snapshot, purged-equivalent count) vs. 7,526,412 (immediately
  post-update, pre-purge) — delta +4,340 at that point in time, matching run 2's
  measured growth exactly (later understood to be the stray rows, since purged;
  the update itself, isolated to this one `unique_key`, is unaffected).

**C1.9 — DQ probe re-run across the full 24-month backfill, by year** (via DuckDB
against the corrected, purged bronze table, 7,522,072 rows total):

| Year | N | closed<created | Closed+null closed_date | missing coords | (0,0)/out-of-bounds | unspecified borough | distinct complaint_type | distinct agency |
|---|---|---|---|---|---|---|---|---|
| 2024 (partial, Aug-Dec) | 1,340,307 | 0.0233% | 0.8485% | 1.1518% | 0.0000% | 0.0720% | 189 | 14 |
| 2025 (full) | 3,655,039 | 0.0250% | 0.1648% | 1.3720% | 0.0000% | 0.0713% | 193 | 15 |
| 2026 (partial, Jan-Aug) | 2,526,726 | 0.0210% | 0.0003% | 2.5169% | 0.0000% | 0.1070% | 196 | 16 |

Real, honest findings, not manufactured: `closed_before_created` and
`unspecified_borough` are stable and low (~0.02-0.03%, ~0.07-0.11%) across all
three years — consistent with Phase 0's single-month baseline, not a 2026 artifact.
`(0,0)/out-of-bounds` coordinates are **exactly 0.0000% in every year** — this
defect genuinely does not exist anywhere in the 24-month window. Two real trends
worth flagging for Phase 3's scorecard: **`missing_coords` more than doubles from
2024 to 2026** (1.15% → 2.52%) — worth investigating whether this tracks a shift in
`open_data_channel_type` mix; and **`closed_null_closed_date` drops sharply**
(0.85% → 0.0003%) — 2024's older, not-yet-reconciled rows carry more of this defect
than near-real-time 2026 rows, the opposite of what "data quality improves with
age/review time" might predict, worth a closer look in Phase 3 rather than assumed
away. `complaint_type`/`agency` vocabularies grow modestly year over year
(189→193→196; 14→15→16), consistent with CLAUDE.md's documented vocabulary-drift
trap.
