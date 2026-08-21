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
- **Peak memory: GAP — not instrumented.** No memory sampling exists in
  `ingest/pipeline.py` for this run, and the backfill will not be re-run solely to
  capture it. If needed later, add sampling (e.g. `resource.getrusage` or a
  background sampler) before the next full-scale run rather than estimating it
  after the fact.
- Retries: zero non-200 responses encountered during the real backfill (the
  same-day earlier mixed-`$where`/`$order` probe did trigger retries, but that was
  a diagnostic query, not part of the backfill itself)
- Criterion 6 (kill/resume): proven on real data mid-run (2026-05/06 windows) — see
  `docs/decisions.md`; zero duplication after resume, checkpoint correctly skipped
  the completed window and restarted the in-progress one
- Initial incremental watermark seeded: `2026-08-18T01:22:36.364129+00:00`
  (earliest window `started_at` across all checkpointed windows, minus the 48h
  buffer — see `docs/decisions.md`'s watermark-handoff correction)

**C1.7 — delta-only proof, five incremental runs across 2026-08-20/21.**

**STATUS: PASS — both idempotency and the insert path are now proven.**

| | Run 2 (pre-fix) | Run 3 (pre-fix) | Run 4 (post-fix) | **Run 6 (2026-08-21, genuine activity)** | **Run 7 (immediate follow-up)** |
|---|---|---|---|---|---|
| Rows fetched | 589,388 | 550,567 | 546,445 | **559,540** | **545,988** |
| Rows updated | not captured | 0 | 0 | **522,213** | **0** |
| Rows inserted | 4,340 (100% out-of-scope, purged) | 0 | 0 | **11,060** | **0** |
| Rows no-op | not captured | 550,567 (100%) | 546,445 (100%) | **26,267 (4.69%)** | **545,988 (100%)** |
| Row count before → after | 7,522,072→7,526,412 | 7,526,412→7,526,412 | 7,522,072→7,522,072 | **7,522,072→7,533,132 (+11,060)** | **7,533,132→7,533,132 (+0)** |
| Snapshot delta | +6 | +0 | +0 | **+25** | **+0** |
| Duration | 1008.5s | 1039.5s | 922.9s | **1349.4s** | **1455.6s** |
| Watermark after | — | — | 2026-08-20T02:23:09.123Z (unchanged) | **2026-08-21T02:49:13.094Z (advanced)** | 2026-08-21T02:49:13.094Z (unchanged) |

Runs 2-4 were all pre-boundary-advance (window either contaminated by the
scope-leak bug or bit-for-bit identical to a prior run) — see
`docs/decisions.md` for why that's correct short-circuit behavior, not a
failure. **Run 6 (2026-08-21T10:46 local start, real fetch after the
`2026-08-21T03:00Z` boundary genuinely passed) is the first run to see actual
new activity:**
- **Insert path: PROVEN.** 11,060 rows inserted; total row count grew by
  **exactly** 11,060 (7,522,072 → 7,533,132) — the criterion 7 pass condition,
  met precisely.
- **Watermark advance: PROVEN.** `2026-08-20T02:23:09.123Z` →
  `2026-08-21T02:49:13.094Z` — confirmed to equal the actual maximum
  `:updated_at` observed in the fetched batch (verified via a live `$group`
  query on the exact window), not an arbitrary or stale value. This code path
  had never fired for real before this run.
- **Short-circuit: confirmed did NOT trigger** (`skipped=False`) — the window
  differed from run 4's (watermark advanced the start bound, even though the
  end boundary was still the same calendar day).
- **Partition-level assertion: confirmed fired correctly on genuine inserts.**
  11,060 inserts and 522,213 updates were spread across many partitions in
  this run; the assertion (added after the Gate 1 write-amplification
  investigation) runs inside every `scoped_upsert()` call and raises loudly
  on any mismatch — the run completed with zero exceptions, meaning it held
  on every touched partition, for the first time exercised on a real,
  non-synthetic insert-bearing run.
- **Run 7 (immediate follow-up, proves criterion 8 against fresh activity,
  not just a static window):** 545,988 fetched, **0 updated, 0 inserted, 100%
  no-op, 0 row/snapshot delta.** Idempotency now proven *after* genuinely
  absorbing new content, closing the gap the original run 3/4 pair left
  open (those only proved idempotency when nothing had changed at all).

**C1.7d — republish-noise measurement, fresh, against the 22.7x baseline.**
Run 6's window spanned `2026-08-18T02:23:09Z` to `2026-08-21T03:00:00Z` =
**3.0256 days**. Expected steady-state volume at ~11,905 rows/day ≈ **36,020
rows**. Observed: **559,540 rows — a 15.5x ratio.** Characterized directly
(not assumed): grouped the fetched window by exact `:updated_at` value —
**522,196 of 522,213 "updated" rows (99.997%) share one exact timestamp,
`2026-08-21T01:33:31.100Z`** — the same ~01:33 UTC daily signature as the
Aug-19 spike (526,605 rows) and the Aug-20 regular touch (~13,537 rows).
**This is another republish artifact, not steady state — a recurring daily
pattern whose magnitude varies substantially day to day (13.5K → 522K → ?),
not a one-time anomaly.** The genuine signal is cleanly separable from it:
11,060 inserts, spread across many distinct `:updated_at` values (not
clustered), consistent with ~7,900/day organic new-row creation — in the
right range given the backfill's own live-pull cutoff (~Aug 20 04:53Z) to
run 6's fetch (~1.4 days later). **Both this run and the original 22.7x
measurement are dominated by the same recurring republish pattern** — two
observations now, both showing it, not steady state in either case.

**C1.8 — update absorption proof (real data, via Iceberg time travel), two
pieces of headline evidence (per Gate 1 review):**

1. **4,521 rows genuinely updated by run 2 — the systemic proof.** Full
   verified-UTC timeline in `docs/decisions.md`. Backfill pulled May 2026
   (01:22:36–01:25:25Z) and June 2026 (01:26:26–01:30:22Z) on 2026-08-20; a
   same-day batch cycle then genuinely changed some of those rows' field
   values sometime before the `03:00Z` window boundary; run 2
   (05:11:18–05:12:05Z) caught and corrected them: `OVERWRITE`
   −666,811/+662,291 → `APPEND` +4,520, then `OVERWRITE` −11,419/+11,418 →
   `APPEND` +1. **4,520 + 1 = 4,521 rows genuinely updated**, across two
   separate partitions. This is a real, measured instance of the exact
   concurrent-with-backfill race condition the watermark-seeding design
   (earliest window `started_at` − 48h buffer) exists to catch — not a
   hypothetical.
2. `unique_key=68857791`: pre-incremental snapshot (#163, 7,522,072 rows)
   shows `status=Open, closed_date=None,
   updated_at=2026-05-15T01:36:13.979Z`; post-run-2 snapshot (#169,
   7,526,412 rows) shows `status=Closed, closed_date=2026-08-18T19:03:01,
   updated_at=2026-08-20T01:33:14.813Z`. Confirmed in-scope
   (`created_date=2026-05-02`), unaffected by the scope-leak bug/purge. Row
   count for this `unique_key`: **exactly 1** in both snapshots — proves
   genuine in-place MERGE update, not a duplicate insert. This is the
   single-row illustration; the 4,521-row finding above is the systemic
   proof of *why* the mechanism matters.

**Criterion 11 (time travel with a real row-count difference) — PASS, now with
three independent, unambiguous pieces of evidence, all still inspectable in
the table's history today:**
1. **#169 vs #170** (the purge's `DELETE`): 7,526,412 → 7,522,072, a real
   −4,340 difference produced by fixing the scope-leak bug — corrected framing
   per Gate 1 review (the original #163-vs-#169 comparison conflated the bug's
   insertion with legitimate growth; kept here as the historical record of
   that correction, not as evidence).
2. **Pre-run-6 vs post-run-6**: 7,522,072 → 7,533,132, a real **+11,060**
   difference from run 6's genuine inserts — clean, unambiguous, not tied to
   any bug-fix narrative.
3. **The 4,521-row update evidence** (criterion 9's headline finding, C1.8
   above) is itself also a valid criterion-11 demonstration: snapshot content
   differs in a real, explainable way between the pre- and post-run-2
   snapshots for those specific rows.

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

**Snapshot retention investigation (per Gate 1 review, investigated not
implemented — full detail in `docs/decisions.md`):**
- 300 snapshots; 2,843 physical data files (907.6 MB), only 1,050 files
  (716.3 MB) referenced by the current snapshot — **1,793 files / 191.3 MB
  superseded**, kept only because nothing expires them.
- 89% of Phase 1's post-backfill disk growth (+214.7 MB vs. the 731 MB
  measured after C1.6) is superseded files, not new data.
- Projected (caveated, no steady-state data yet): ~64.6 MB of new superseded
  data per correction event at run-2's scale — **~23.6 GB/year if untouched**,
  vs. 731 MB total live data.
- PyIceberg 0.11.1's `expire_snapshots()` verified (source read) to remove
  only snapshot-log entries — **zero physical file deletion**, no
  compaction/orphan-file utility exists anywhere in the package.
- Phase 7 consequence: S3 mirror/Athena cost planning is sized on 731 MB —
  wrong if no retention policy runs before then.
