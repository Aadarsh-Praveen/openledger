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

## Phase 2

**C2.1 — dbt→DuckDB→Iceberg read path (measured live, throwaway test project):**
- `dbt show` against a direct `iceberg_scan()` model returned **7,533,132**
  rows — exact match to bronze's known count. Extension load +
  `unsafe_enable_version_guessing` confirmed to persist across separate dbt
  CLI invocations.
- Full-table `count(*)`: 0.088s. Partition-pruned `count(*)` (1 month):
  0.061s. Full-table all-columns materialization: **54.2s**.
- Parquet-export fallback: 9.5s export + 41.6s read-back = 51.1s total (or
  41.6s/run if export is amortized) — **~23% faster than direct reads, not
  dramatically so.**
- **Recommendation:** keep direct Iceberg reads; do not build the export
  fallback. Full reasoning in `docs/decisions.md`.

**Phase 7 benchmark baselines** (captured now per Gate 2 review, so the
DuckDB-vs-Athena comparison in Phase 7 has a same-methodology local number to
compare against, not a re-measurement done differently under time pressure):

| Query | DuckDB time | Bytes scanned |
|---|---|---|
| Full-table `count(*)` | 0.088s | **0** — metadata-only, Iceberg manifest statistics satisfy the count without reading any Parquet data |
| Partition-pruned `count(*)` (1 month, ~335k rows) | 0.061s | **0** — same metadata-only path |
| Full-table materialization, all columns (7,533,132 rows) | 54.2s | full data volume (731 MB data files as of this measurement) |

**This is the exact comparison Phase 7 is built on, stated explicitly so it
isn't lost**: the two `count(*)` figures are local metadata reads that touch
zero bytes of actual data — Athena has no equivalent free path for the
identical query; it will scan and bill for the underlying data (or at best
partition-level pruning, never a pure manifest-stats answer), so a naive
"DuckDB did this in 0.09s, Athena took Xs" comparison is only fair once
Athena's bytes-scanned and dollar cost are reported alongside its latency,
not latency alone.

**Environment, for a fair comparison:** Apple M3 Pro, 18 GB RAM, arm64/macOS
15.7.3, DuckDB **1.5.5** (the 1.5+ line, not 1.4 LTS — see Phase 0's
versions.md), dbt-duckdb 1.11.0, bronze at 7,533,132 rows / 731 MB (measured
state as of Phase 1's completion, before Phase 2's own testing added
snapshot-retention overhead — see the Phase 1 retention investigation for
that separate number).

**C2.10 — Final row counts, test results, build duration (prod target,
full `dbt build`, models + tests together):**

| Model | Layer | Rows |
|---|---|---:|
| `stg_service_requests` | staging | 7,533,132 |
| `int_request_resolution` | intermediate | 7,533,132 |
| `int_request_geography` | intermediate | 7,533,132 |
| `dim_agency` | mart | 16 |
| `dim_complaint_type` | mart | 1,278 |
| `dim_location` | mart | 79 |
| `dim_date` | mart | 1,230 |
| `fct_service_requests` | mart | 7,533,132 |

Staging reconciles exactly to bronze (7,533,132 = 7,533,132); the fact
table's row count equals its distinct `unique_key` count with zero null
foreign keys across all four dimension joins (the grain-violation bug from
C2.6/C2.7 is fixed and re-verified here).

**Test results** — 5 table models + 3 view models + 42 data tests:

| Target | Models | Tests | Result | Full build duration |
|---|---|---|---|---|
| prod | 8 (5 table, 3 view) | 42 | 49 PASS / 1 WARN (by design) / 0 ERROR | ~10–12s |
| dev (90-day slice) | 8 | 42 | 49 PASS / 1 WARN (by design) / 0 ERROR | ~7s |

The one WARN is `assert_closed_date_after_created_date`, configured to warn
at any failure count and error only above 2x the known defect rate — see
`docs/decisions.md` C2.9. It fired at 1,763 rows (prod, ≈0.0242%) and 354
rows (dev), consistent with the same underlying ~0.02% rate at each
target's row count, never approaching the `error_if: '>3500'` ceiling.

Test breakdown by category (42 total, via `dbt list --resource-type test`):
8 `unique`, 27 `not_null`, 4 `relationships`, 3 hand-written singular SQL
tests (`assert_resolution_hours_null_when_censored`,
`assert_closed_date_after_created_date`,
`assert_staging_reconciles_to_bronze`). Separately (not counted in the 42 —
validated at build time, not as test nodes), **5 model contracts enforced**
across the marts layer: `dim_agency`, `dim_complaint_type`, `dim_location`,
`dim_date`, `fct_service_requests` — all columns explicitly typed, per
phase-2.md's "marts only" scope for Phase 2.

## Phase 3 (C3.1–C3.4; C3.5 backtests recorded in `docs/decisions.md`/`docs/findings.md`,
not yet reduced to a single re-runnable metric — see that section for the raw
counts pending H3.2 approval and code implementation)

**C3.1 — Soda/DuckDB dependency resolution.** Option 1 (separate venv), chosen
after empirical testing per phase-3.md's stated preference order — `soda-core-duckdb`
3.5.6 (the latest release at the time of checking) still hard-pins `duckdb<1.1.0` in
its published wheel metadata, confirmed directly rather than assumed. Two
independent, non-merged environments: `.venv/` (main project, DuckDB **1.5.5**) and
`.venv-soda/` (Soda only, DuckDB **1.0.0**). Cross-version read compatibility (1.0.0
reading a `.duckdb` file written by 1.5.5) verified empirically, not assumed — see
H3.1 confirmation below for the live re-test of this exact claim.

**C3.2 — Model contracts, all layers.** **8 models, 101 columns at the time C3.2 was
built** (49 staging + 14 intermediate + 38 marts), all with `contract: enforced:
true` and an explicit `data_type` on every column (verified by counting
`data_type:` entries in each layer's schema file): the 5 marts-layer contracts were
already in place as of Phase 2; C3.2's addition was the 1 staging + 2 intermediate
models, +63 columns. **Updated to 103 columns by C3.7** (`is_undated_closure` added
to `int_request_resolution` and `fct_service_requests`, +1 column each) — current,
live count verified every build by `assert_dq_scorecard_contract_counts_match_schema.sql`,
not left to drift silently; see `docs/decisions.md`, C3.6, for a self-caught error
in this exact number and why that test exists. A deliberate contract break
(mismatched `data_type`) was tested and shown to fail the build, then reverted — see
`docs/decisions.md`, C3.2, for the captured failure output.

**C3.3 — dbt unit tests.** **7 unit tests**, all against `int_request_resolution`,
all passing: normal resolution-hours computation, the closed-before-created defect
passed through unsuppressed, an open request's censoring, a closed-but-unsettled
request's forced-null resolution, and the 45-day `is_settled` boundary tested at
exactly 44/45/46 days (inclusive on 45, per the model's `<=`). A real bug —
`is_settled` resolving against DuckDB's session `TimeZone` setting instead of the
project's documented `created_date_timezone` — was found and fixed in
`int_request_resolution.sql` while writing these tests (see `docs/decisions.md`,
C3.3). A separate DST-boundary case (ambiguous fall-back hour, nonexistent
spring-forward hour) could not be expressed as a native dbt unit test — the source
relation is an inline `iceberg_scan()` expression with nothing to introspect — so it
is implemented instead as the singular data test
`assert_timezone_localization_dst_boundaries.sql`, run in every `dbt build`.

**C3.4 — Distributional checks.** **6 checks** in `quality/soda/checks/distributional_checks.yml`
(row-count volume, missing-coordinate rate, resolution-hours distribution, closure
rate by cohort age, complaint-type composition drift, agency composition drift),
every threshold traced to its measured 24-month range in `docs/decisions.md`, C3.4.
Live scan result (run 2026-08-22, against the current bronze state — see note
below): **5/6 PASSED, 1/6 FAILED**. The one failure (`Row-count volume for the most
recent fully-settled-by-publish-lag day is within [4000, 28000]`) is real and
explained, not a check defect: bronze's last ingested row is `2026-08-20 01:50:51`
(no incremental run since the session interruption), so `created_date::date =
today_ny - 2` (2026-08-20) contains only 345 rows — a partial day — against a
[4000, 28000] band derived from full days. This is the check correctly detecting
that bronze is stale relative to wall-clock time, not a defect in the underlying
data; re-running after the next incremental ingestion is expected to pass.

## Phase 3 (C3.5–C3.8; final, post-implementation numbers, 2026-08-23)

**C3.5 — five detectors, all implemented as dbt models**
(`dbt/models/marts/quality/dq_detector_*.sql`), each computing the full
24-month backtest every run. Reproduced against the H3.2/H3.2b journal with
two corrections found and fixed in the journal, not the code (full detail
in `docs/decisions.md`):

| Detector | Backtested firings | Correction from original journal entry |
|---|---|---|
| (a) Undated-closure rate (redesigned) | DHS: 25/25 monthly cumulative scans. All 15 other agencies: 0/25 (final, point-in-time threshold) | Two corrections: journal originally claimed 0 non-DHS firings (never checked); then found DSNY firing 10/25 under a static threshold; then found that 10/25 was itself a look-ahead bug in the threshold, fixed 2026-08-24 — see `docs/decisions.md` |
| (b) Composition-drift (YoY) | 2 of **194** evaluations (STREET CONDITION Mar 2026 +5.88pp; NOISE-RESIDENTIAL Jan 2026 −12.10pp) | Journal's back-of-envelope 195 assumed every top-15 type has nonzero volume every eligible month; one cell (WATER SYSTEM, partial Aug 2026) is empty |
| (c) Vocabulary-drift | 2 new agencies (`OOS`, `NYC311-PRD`), 17/23 months with ≥1 new complaint type | None — matches exactly |
| (d) Settlement-completeness | 0 fails/warns across 4 eligible cohorts (Feb 92.06%, Mar 92.98%, Apr 93.92%, **May 2026 95.25% — newly eligible since the journal was last written**, one day of wall-clock time having aged it past the 90-day mark) | None — matches, plus one new cohort as expected |
| (e) Mass metadata-touch | 6 of 180 eligible nights (Dec 2025 migration 4.35M rows; 5 single-agency batch-volume nights) | None — matches exactly, including all 6 dates and row counts |

**C3.6 — the DQ scorecard mart**, `fct_data_quality_checks`: **103 rows**
on this run (8 contract + 7 unit + 6 distributional + 82 detector),
incremental (accumulates one new row-set per `run_date`). DHS's
permanently-firing detector (a) row carries `status='acknowledged'`
(not `fail`), sourced from `dbt/seeds/quality_acknowledgments.csv`
(acknowledged 2026-08-22) — the true measured value (17.2908%) stays
visible, just recontextualized. The 6 live-recomputed distributional rows
independently cross-check Soda's own 6 checks: both report the identical
1-of-6 failure for the identical reason (stale bronze) on this run.

**C3.7 — DHS exclusion, quantified.** 17,356 affected rows, `created_date`
2024-08-19 through 2025-05-06 (America/New_York) — auditable directly via
`fct_service_requests.is_undated_closure`. DHS's closure rate among settled
requests: **80.97% including the backlog rows in the settled-not-closed
bucket, 98.61% excluding them entirely — a +17.65 percentage point
delta.** See `docs/findings.md` for the full statement.

**C3.8 — suite duration, final.** `dbt build --profiles-dir . --target prod`
(run from `dbt/`; 10 table models incl. 5 detectors + the scorecard, 3 view
models, 1 seed, 72 data tests, 7 unit tests = 94 nodes): **13.9s**, 93 PASS
/ 1 WARN (the known ~0.02% closed-before-created rate) / 0 ERROR.
`quality/run_soda.py` (6 checks, run second — never overlapping `dbt
build`, per the H3.1(c) lock-contention finding): **2.0s**. Full
sequential suite: **~16s** — an order of magnitude under any Phase 6
scheduling concern. One command, two necessarily-separate steps
(`docs/decisions.md`, C3.8, states why merging them is not possible).

## Phase 4 (MetricFlow semantic layer; final, post-H4.2 numbers, 2026-08-23)

**C4.1 — toolchain**: `dbt-metricflow==0.14.0` + `metricflow==0.212.0`,
both Apache-2.0, installed into the main venv (no conflict, unlike Soda).
No dbt Cloud involved (confirmed via environment + traceback inspection).

**C4.2 — semantic models**: **5** (`sm_service_requests` over the fact,
grain one row per `unique_key`; `sm_agency`, `sm_complaint_type`,
`sm_location`, `sm_date` over the four dimensions). `mf validate-configs`:
all 6 validation passes green (manifest parse, semantic model, dimension,
entity, measure, metric — the last four against the live DuckDB
warehouse, not just the YAML).

**C4.3/H4.2 — metrics**: **13 registered, 9 are real analytical
deliverables** (`median_resolution_hours`, `p90_resolution_hours`,
`closure_rate`, `settlement_rate`, `censored_count`, `request_count`,
`naive_median_resolution_hours`, `naive_closure_rate`,
`closure_rate_excl_backlog`); 4 exist only as ratio-metric plumbing
(`closed_count`, `closed_and_settled_count`, `settled_count`,
`settled_count_excl_backlog`). `complaint_type_share` removed at H4.2
(confirmed non-functional — returns 1.0 for every group; no
percent-of-total metric type in this MetricFlow version).

**C4.4 — correctness invariant**: proven at the SQL-generation level, not
just by querying and eyeballing. Read generated SQL for
`median_resolution_hours` across 3 group-by shapes (by agency, by month,
none) — the filter predicate is character-for-character identical and in
the identical structural position (inside the row-level projection,
before any `GROUP BY`) in all three.

**C4.4/H4.2 — naive-vs-correct, two verified pairs, two different traps**:

| Pair | Trap exposed | Correct | Naive | Gap |
|---|---|---:|---:|---:|
| `median_resolution_hours` vs `naive_median_resolution_hours` | Settlement/censoring (survivorship bias) | 8.00h | 7.00h | −1.00h (~12.5% relative), dataset-wide |
| `closure_rate_excl_backlog` vs `naive_closure_rate` | Undated-closure backlog | 98.60% | 80.97% | **+17.63pp, DHS specifically** (matches C3.7's independently-derived 80.97%/98.61% almost exactly) |

Both pairs verified to actually differ before being finalized — the first
draft of each did NOT (the resolution-hours draft used the already-
censored `resolution_hours` column, silently identical to the correct
measure, 0 disagreeing rows; the closure-rate draft used an unnested
`closed_count` numerator, producing impossible >100% rates for several
agencies). Both root causes found and fixed before shipping, not after.

**C4.5 — reconciliation**: 9 real metrics × (dataset-wide + all 16
agencies) queried via MetricFlow and cross-checked against independent
hand-written DuckDB SQL. **0 discrepancies** across every value checked,
on the corrected definitions.

**C4.6 — interface doc**: `docs/metrics_interface.md`.

**A second occurrence of Phase 3's partial-parse staleness finding**:
removing `complaint_type_share` and rebuilding did not remove it from
`mf list metrics` until `target/partial_parse.msgpack` was deleted
manually — the same class of bug as the Phase 3 unit-test date staleness,
now confirmed to also affect semantic-layer metric removals, not just
Jinja `datetime.now()` re-renders. Reinforces the existing Phase 6
scheduling note (`docs/decisions.md`, C3.3/C4.6).
