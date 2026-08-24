# Engineering Journal

Format per entry: what was decided or discovered / what the evidence was / what it
changes downstream.

## Phase 0

### Backfill window: 24 months (provisional)

- **Decided:** Backfill window is 24 months of `created_date`.
- **Evidence:** H0.2 states the full `erm2-nwe9` dataset (2020-01-01 to present) is
  22.2M rows, ~44 columns. That's roughly 6.6 years of data, so 22.2M / 6.6 ≈ 3.4M
  rows/year, and 24 months ≈ 6.7M rows. Two full seasonal cycles supports
  year-over-year comparison. The app-token throttle is reportedly ~1,000
  requests/hour, so ingestibility depends on the real page-size cap.
- **Decision rule (applied mechanically against the C0.3.3/C0.3.6 measurements,
  without asking first):**
  - measured page-size cap ≥ 10,000 rows/page → keep 24 months
  - cap 1,000–10,000 rows/page → recommend 12 months, state the revised row estimate
  - cap < 1,000 rows/page → stop and report; the incremental design needs rework
- **Outcome:** Measured page-size cap was **50,000 rows/page** (`scripts/verify_source.py`,
  run 2026-08-19) → rule says **keep 24 months**. Actual row count in the 24-month
  window: **7,511,072** (higher than the 6.7M rough estimate, still well within budget
  — at the observed 50,000-row cap that's only 151 pages, ~65 seconds of pure request
  time at median latency). See `docs/source-notes.md` C0.3 for full findings.

### Python toolchain: 3.14.7 vs 3.12 compatibility probe

- **Decided:** Use Python 3.14.7 (the system default) as the project interpreter. No
  fallback to 3.12 was needed.
- **Evidence:** Ran `pip install --dry-run` in a scratch venv on 3.14.7 for three
  package sets — Phase 0's actual install list, plus Phase 2's (`dbt-core`,
  `dbt-duckdb`) and Phase 3's (`soda-core-duckdb`) as resolution-only checks (nothing
  from Phase 2/3 was installed). All three resolved without error. Every native
  package needed for Phase 0 (`duckdb`, `pyarrow`, `mmh3`, `pyroaring`,
  `pyiceberg-core`, `zstandard`) had a prebuilt `cp314` wheel — no source builds were
  required for the Phase 0 install itself. Full detail in `docs/versions.md`.
- **Downstream effect:** Project venv (`.venv/`) built directly on 3.14.7. One risk
  surfaced by the probe: `soda-core-duckdb` 3.5.6 hard-pins `duckdb<1.1.0`, which
  conflicts with the `duckdb` 1.5.5 pinned here. It resolved in isolation (and even
  built duckdb 1.0.0 from source rather than erroring) only because it was checked as
  its own pip invocation, not installed alongside the others. Phase 3 will need a
  plan for this — most likely a separate virtualenv/process boundary between Soda and
  the main dbt-duckdb environment, since nothing requires them to share one Python
  process. Not actioned now; Soda isn't installed until Phase 3.

### DuckDB version line: 1.5.5, not 1.4 LTS

- **Discovered:** `pip install duckdb` on 3.14.7 resolved to **duckdb 1.5.5**
  (released 2026-07-22), which is on the 1.5+ line, not the 1.4 LTS line.
- **Evidence:** `pip freeze` output and `docs/versions.md`.
- **Downstream effect:** The partitioned-table UPDATE/DELETE restriction that applies
  on DuckDB 1.4 LTS is lifted on 1.5+ (per CLAUDE.md's known-traps note). Any local
  DuckDB-side mutation logic in later phases can rely on that being lifted — though
  bronze writes still go through PyIceberg regardless, per the locked architecture
  decision (DuckDB path-based Iceberg writes are unsupported).

### Socrata `$where`/pagination behavior matches the incremental design (C0.3)

- **Discovered:** All C0.3 checks passed against the live API on 2026-08-19.
  `$where` on `created_date` returned zero rows outside a requested bound. `$limit`
  accepted up to 50,000 rows in a single page (not clamped to the assumed
  1,000–5,000). `$offset` pagination with `$order=created_date,unique_key` was
  stable — zero `unique_key` overlap between consecutive pages, and page 1 was
  byte-for-byte reproducible on re-pull. 20 sequential requests hit zero throttling
  and no rate-limit headers; median latency 0.426s.
- **Evidence:** `scripts/verify_source.py` output, transcribed into
  `docs/source-notes.md` C0.3 table.
- **Downstream effect:** The watermarked incremental design (Phase 1) is validated —
  nothing here forces a redesign. Phase 1's pagination loop should use a large page
  size (the plan's 1,000–5,000 assumption was overly conservative), which will cut
  the number of HTTP requests substantially.

### Single-row API response does not show all 44 dataset-page columns

- **Discovered:** Q1 of `scripts/verify_source.py` pulled one live row and it
  contained 32 present JSON keys, not 44.
- **Evidence:** Printed column list in the C0.3 run; H0.2's dataset-page column count
  is 44.
- **Downstream effect:** Not a contradiction — Socrata's JSON API omits fields that
  are null for a given row rather than emitting `null` values. Phase 1's schema
  handling must tolerate any optional column being absent on a per-row basis, not
  assume a fixed 44-key shape on every response.
- **Resolved in Phase 1 (C1.2):** the authoritative schema is neither 32 nor 44 —
  it's **48**, per Socrata's dataset metadata endpoint
  (`/api/views/erm2-nwe9.json`), which lists every defined column regardless of
  per-row nullness. `48 − 4 = 44`: the portal landing page's displayed count
  excludes the 4 `:@computed_region_*` computed-geography columns that the metadata
  API includes. Full column list with types in `docs/source-notes.md` C1.2 — that's
  the bronze schema source of truth, not H0.2's 44 or this entry's 32.

### DQ baseline: several classic 311 defects barely exist in current data (C0.4)

- **Discovered:** On a real one-month sample (July 2026, 342,892 rows, full window
  pulled — not a sub-sample): closed-before-created 0.019%, closed-status-with-null-
  closed-date 0.0003%, out-of-bounds/zero coordinates **0%** (0 of 342,892), missing
  lat/long 1.975%, unspecified/null borough 0.134%. 176 distinct `complaint_type`
  values, 14 distinct `agency` values.
- **Evidence:** `scripts/probe_data_quality.py` output, transcribed into
  `docs/source-notes.md` C0.4 table.
- **Downstream effect:** Per CLAUDE.md's standing rule to report real findings rather
  than manufacture them: the closed-before-created and out-of-bounds-coordinate
  defects that portfolio narratives often lean on are rare-to-absent in current
  (2026) 311 data. Phase 3's DQ scorecard should center on what's actually material
  here — missing coordinates (~2%) and, more marginally, unspecified borough
  (~0.13%) — and report the others honestly as low-incidence rather than inflate
  them.

### DuckDB `iceberg_scan` path/version-guessing quirks (C0.5)

- **Discovered:** Two platform quirks surfaced getting DuckDB to read a
  PyIceberg-written table:
  1. `iceberg_scan(<metadata.json path>)` fails — this DuckDB iceberg extension
     build resolves the path argument as the **table root** and appends
     `/metadata/...` itself; passing the `metadata.json` file path directly produces
     a `<file>.metadata.json/metadata/...` path-join `IOException`.
  2. Even with the table root, DuckDB refused to auto-locate the latest snapshot
     ("no version-hint could be found... globbing... disabled by default") because
     PyIceberg's `SqlCatalog` doesn't write a `version-hint.text` file. Fixed with
     `SET unsafe_enable_version_guessing = true;` before the scan.
- **Evidence:** `scripts/verify_iceberg_stack.py` run transcript (two intermediate
  failures before the working form); final row counts matched (5 = 5) between
  PyIceberg and DuckDB.
- **Downstream effect:** Any later-phase code or docs that read a local Iceberg table
  from DuckDB (dev loop, Phase 7 DuckDB-vs-Athena benchmark) must either pass the
  table root with `unsafe_enable_version_guessing` set, or ensure a version-hint file
  exists. Recorded here so Phase 1+ doesn't rediscover this.

## Phase 1

### Watermark field: recommend `:updated_at`, with a lag caveat — PENDING H1.1 APPROVAL

- **Recommended:** Watermark the incremental pull on Socrata's `:updated_at` system
  field instead of `created_date`, per the plan's working hypothesis in phase-1.md.
- **Evidence (`scripts/probe_watermark.py`, run 2026-08-19, full findings in
  `docs/source-notes.md` C1.1):**
  - `:updated_at` exists, is selectable, is filterable in `$where` (verified by
    content — 0 of 5,000 rows fell outside a requested bound, not just HTTP 200),
    and is orderable in `$order` (response came back strictly sorted).
  - It genuinely diverges from `created_date`: 100% of a 5,000-row sample of rows
    created ~3.5–4 months ago had `:updated_at` more than 1 hour later than
    `created_date`. This is far from the "near zero" condition phase-1.md names as
    the kill criterion, so the strategy does not fail on that test.
  - **Caveat surfaced beyond the plan's original six questions:** `:updated_at`
    does not move at the instant of content change. Across the same sample, only 91
    distinct `:updated_at` values appear for 5,000 rows (one value alone covers
    48.94% of the sample), clustering near a consistent time-of-day (~01:33 UTC) —
    the signature of a periodic batch republish, not row-level real-time tracking.
    Measured directly against `closed_date` for 4,730 genuinely closed rows in the
    window: `:updated_at` lags `closed_date` by **min 2.53 days, median 6.71 days,
    max 92.42 days**. No row in this sample showed same-day closure visibility via
    `:updated_at`.
  - Staleness risk quantified: of 294,770 rows created in the last 30 days,
    **23.37%** are still open (non-Closed) at pull time — that's the share of rows
    a `created_date`-only watermark would strand permanently, which is the problem
    `:updated_at` (with its lag) still solves, just not instantly.
  - Other candidate field found: `resolution_action_updated_date` (among the 44
    dataset columns) looks purpose-built as a modification timestamp but was not
    independently verified for `$where`/`$order` behavior — out of C1.1's scope. No
    `:version` field exists.
- **Why this changes the plan, not just confirms it:** phase-1.md's framing implied
  `:updated_at` would track modifications closely; the real behavior is
  eventually-consistent with a multi-day-to-multi-week lag floor. That doesn't
  invalidate the watermark strategy — Iceberg MERGE will still absorb the update
  whenever `:updated_at` eventually advances, and no row is permanently stranded —
  but it does mean the Phase 6 freshness SLA target should be framed around
  "eventually captured," not "same-day closure visibility." A `resolution_hours`
  metric computed from a same-day pull will systematically undercount recent
  closures until the lag catches up.
- **Status:** **APPROVED with conditions (H1.1, 2026-08-19/20).** `:updated_at` is
  adopted as the watermark field. The human required one additional probe (C1.1b,
  below) before C1.2, plus three design requirements for C1.4. The
  eventual-consistency framing (2.53-day lag floor, 6.71-day median, 92-day tail) is
  confirmed correct and carries into the Phase 6 freshness SLA wording and the
  README limitations section.

### C1.1b — `:updated_at` is a batch-republish timestamp, not row-level tracking

- **Discovered:** Sampled rows with `created_date` in 2021 **and** `closed_date` in
  2021 (long-settled, no plausible reason to have changed recently) —
  **3,047,736** such rows exist; a 50,000-row page-capped sample was pulled. Their
  `:updated_at` values are **99.99% in December 2025** (49,997 of 50,000; the
  dominant bucket is 2025-12 specifically), **0% in 2021**.
- **Evidence:** `scripts/probe_watermark_republish.py` run 2026-08-19/20; full table
  in `docs/source-notes.md` C1.1b. December 2025 is the exact month CLAUDE.md records
  as the dataset's restructuring/split date.
- **Verdict (per the pre-agreed decision rule):** **BATCH-REPUBLISH.** `:updated_at`
  advances on periodic/bulk republish events regardless of whether a row's content
  actually changed — the Dec 2025 restructuring bulk-touched effectively the entire
  historical dataset. This is not row-level modification tracking.
- **Downstream effect — STOP triggered per the pre-agreed rule:** The watermark
  still prevents permanent staleness (monotonic; every genuinely-changed row is
  eventually caught). **But naive "rows added since last run" volume estimates
  derived from `:updated_at` are invalid as a signal of genuine change** — a future
  bulk republish landing inside an incremental run's window would make that run look
  like a near-full backfill even though nothing meaningful changed. **The C1.7
  delta-only gate criterion needs redefinition before C1.4 is written**, per H1.1's
  explicit instruction. This is flagged back to the human rather than resolved
  unilaterally — see the stop report accompanying this entry. Not proceeding to
  C1.2 until this is resolved.

### Expected rows-per-incremental-run (feeds C1.7's gate, with caveat)

- **Measured:** Daily counts of rows with `:updated_at` landing in each of the 7
  days preceding the probe: 537,069 / 13,512 / 14,110 / 11,737 / 9,507 / 0 / 22,561.
  Average across all 7: 86,928/day. Average excluding the day-1 outlier (which looks
  like another in-progress bulk republish, ~40x the next-highest day): **11,905/day**.
- **Evidence:** `scripts/probe_watermark_republish.py`, same run.
- **How to apply:** **Do not use either number as a hard pass/fail threshold for
  C1.7 until the delta-only gate criterion is redefined** (see above) — both figures
  are contaminated by unknown amounts of batch-republish noise, not verified
  genuine-change volume. Recorded here only so the eventual redefinition has a
  concrete starting number (~12k/day plausible steady-state, with observed spikes to
  500k+/day during republish events) rather than no data at all.

### C1.4 design requirement A — watermark comparison is `>=` with a lookback buffer

- **Decided:** The incremental query predicate must use `:updated_at >= (watermark -
  buffer)`, never a strict `>` against the raw last-seen watermark value. Overlap
  with the previous run is expected and absorbed by `upsert()` idempotency on
  `unique_key`.
- **Rationale/evidence:** C1.1 found a single `:updated_at` value covering 48.94% of
  a 5,000-row sample — a strict `>` boundary landing anywhere inside that cluster
  would silently strand every other row sharing that exact timestamp (they'd never
  again satisfy `> watermark` once the watermark advances past that instant).
- **Buffer size: 48 hours**, chosen from C1.1/C1.1b evidence: republish events
  observed so far are approximately daily-granular (C1.1's dominant clusters each
  sat on a distinct calendar day, ~01:33 UTC) but not perfectly regular — C1.1b's
  7-day window included a day with 0 rows and a day with a 40x spike, meaning a
  single day's buffer is not obviously enough headroom if a run is skipped or a
  republish lands late. 48 hours covers a full event's cluster plus one full day of
  schedule slack, at negligible cost since MERGE absorbs the overlap for free.
  Revisit if C1.7's actual second/third-run measurements show this is insufficient
  (rows reappearing that shouldn't) or excessive (re-fetch volume dominating runtime).

### C1.4 design requirement B — timezone normalization rule

- **Decided:** Canonical rule for the codebase: **all watermark comparisons happen
  in UTC.** `:updated_at` is used as-is (already offset-aware UTC, confirmed by its
  `Z` suffix). `created_date` is **assumed to be Eastern local time
  (`America/New_York`)** and is used as-is for `day(created_date)` partitioning —
  never shifted or reinterpreted as UTC.
- **Evidence for the `created_date` timezone assumption:** live hour-of-day
  call-volume distribution for June 2026 (`date_extract_hh(created_date)`, grouped)
  shows a trough at 03:00–05:00 as-stored — the typical overnight lull for a US
  city's *local* time. If these values were actually UTC needing a −4/−5 hour shift
  to Eastern, the true local trough would fall at 23:00–01:00 as-stored, which is
  implausible for call volume. Full distribution in `docs/source-notes.md` C1.1b.
  This is inferential (pattern-based), not a documented Socrata guarantee — flagged
  as an assumption, not a fact, and it is **not load-bearing for the watermark
  itself** (which runs entirely on already-unambiguous `:updated_at`). It matters
  for partition-day boundaries and any local-time SLA framing in later phases.
  Whether NYC 311's `created_date` is DST-aware (EDT vs EST split) or a fixed offset
  is unverified — out of scope here, revisit if a partition-boundary bug surfaces
  around a DST transition.

### C1.4 design requirement C — `resolution_action_updated_date` cross-check field verified

- **Decided:** `resolution_action_updated_date` is confirmed usable as a
  `$where`/`$order`-capable secondary field for C1.8's update-absorption proof (not
  as a replacement watermark).
- **Evidence:** Live bounded `$where` window (2026-08-01–2026-08-07) returned 5,000
  rows, 0 outside the window; `$order` on the same field returned strictly sorted
  ascending results. `scripts/probe_watermark_republish.py`, requirement-C section.
- **Kept as the C1.8 cross-check field**, per H1.1 follow-up review, now that
  `$where`/`$order` are confirmed.

### Two corrections to the C1.1b interpretation (human review, before C1.2)

**Correction 1 — "every row touched nightly" was not supported by the numbers.**
The original write-up said `:updated_at` "advances on periodic republish regardless
of whether a row changed," which could be misread as "the nightly cycle re-stamps
the whole table." That's wrong on its own arithmetic: if the cycle stamped all
7.5M backfill rows nightly, daily counts would read ~7.5M, not the observed
~11,905/day baseline. **Correct reading: the nightly cycle batch-stamps whatever
rows changed since the last cycle, using the batch run's own timestamp as the
stamp for all of them** — it is a *batch-stamped change timestamp*, not a
wholesale republish. Steady-state volume is **11,905 / 7,511,072 ≈ 0.16% of the
backfill table per day**. Consequence: the pre-upsert diff-filter design option
(ii) is insurance against occasional large-cohort recurrence, not a steady-state
optimization — do not over-build it. (It turned out unnecessary regardless; see
C1.3b below.)

**Correction 2 — the original 7-day daily buckets were boundary-misaligned and
therefore unreliable, including the reported spike.** The buckets were cut at
midnight UTC; the actual batch-stamp cluster lands at ~01:33–02:03 UTC, so a
midnight cut can split one day's cluster across two reported buckets, or (in the
original run, which used a rolling "now minus N days" window rather than fixed
calendar boundaries) shift a cluster in or out of a bucket depending on exact
script execution time. A genuine zero-count day at a ~12k/day baseline is a red
flag for exactly this kind of artifact.

**Re-run with windows aligned to the observed ~02:00 UTC batch boundary**
(`scripts/probe_watermark_republish.py`'s ad hoc realignment, run 2026-08-20):

| Window (02:00 UTC boundaries) | Rows |
|---|---|
| 2026-08-18T02:00 – 2026-08-19T02:00 | **526,651** |
| 2026-08-17T02:00 – 2026-08-18T02:00 | 13,512 |
| 2026-08-16T02:00 – 2026-08-17T02:00 | 14,110 |
| 2026-08-15T02:00 – 2026-08-16T02:00 | 11,737 |
| 2026-08-14T02:00 – 2026-08-15T02:00 | 9,507 |
| 2026-08-13T02:00 – 2026-08-14T02:00 | 0 |
| 2026-08-12T02:00 – 2026-08-13T02:00 | 22,561 |

The middle five values are **bit-for-bit identical to the original midnight-cut
run** — the daily batch cluster sits safely inside a ~01:33–02:03 UTC window that
neither a midnight nor a 02:00 cut happens to split, so that baseline reading
survives unchanged and is trustworthy: **~11,905/day excluding the zero day and
the spike (average of 13,512/14,110/11,737/9,507), ≈0.16% of the backfill table.**
The zero-count day followed by a near-double day (22,561 ≈ ~2× baseline) is
consistent with one skipped batch cycle whose backlog was absorbed into the next
run — not a measurement artifact.

**The spike survived realignment** (526,651, down only slightly from the original
537,069) — **it is real, not a boundary artifact.** Grouping the spike window by
exact `:updated_at` value confirms it: **526,605 of 526,651 rows (99.99%) share one
exact timestamp, `2026-08-19T01:33:23.553Z`** — the same time-of-day as every other
day's dominant cluster, just a vastly larger cohort that specific run. Per the
pre-agreed rule, this makes design option (ii) evidence-justified as insurance
against spike recurrence — see C1.3b for why it still isn't built.

### C1.3b — PyIceberg's `upsert()` already skips no-op rows; no diff-filter needed

- **Tested:** `scripts/probe_upsert_noop.py` — created a 3-row table, then (1)
  called `upsert()` with a byte-identical copy of all 3 rows, and (2) called
  `upsert()` with a mixed batch (1 genuinely changed row + 2 identical rows).
- **Result:** Test 1 (pure no-op batch): `rows_updated=0`, `rows_inserted=0`,
  snapshot count unchanged (1→1), data file count unchanged (1→1) — **no write
  occurred at all.** Test 2 (mixed batch): `rows_updated=1` (correctly identifies
  only the genuinely changed row among 3), new snapshot created, exactly 1 new data
  file written.
- **Finding:** PyIceberg's `upsert()` already performs row-level value comparison
  internally as part of its matched-row join and **skips writing rows whose
  incoming values are identical to the existing row.** This is design option (ii),
  already provided by the library.
- **Consequence:** No hand-rolled pre-upsert diff-filter will be built. C1.4's
  ingestion loop upserts everything `:updated_at` returns (scoped to touched
  partitions per C1.5); PyIceberg's own upsert-join logic absorbs both the
  steady-state ~0.16%/day baseline and any future large-cohort republish spike
  without redundant writes. This also means the spike evidence above, while real,
  does not require new ingestion-side machinery — it does inform buffer/timeout
  sizing below, since a 500k+-row upsert batch takes materially longer than a
  12k-row one and C1.4's retry/timeout handling should account for that.

### C1.4 design requirement A (revised) — buffer size and window-boundary alignment

- **Decided:** Lookback buffer is **48 hours**, and incremental-run query window
  boundaries are anchored to **03:00 UTC**, not midnight.
- **Buffer rationale:** the batch-stamp cycle runs nominally once per ~24h at
  ~01:33 UTC, but the corrected 7-day series shows a real skipped cycle (zero-count
  day) whose backlog was absorbed into the following run (~2× baseline). A 24-hour
  buffer would not reliably cover a skipped-cycle backlog; 48 hours (two full
  cycles) does, with MERGE/upsert idempotency absorbing the resulting overlap at
  effectively no cost (confirmed by C1.3b — re-processing already-seen rows with
  unchanged values is a no-op write).
- **Boundary-alignment rationale:** every observed batch cluster in this
  investigation (spanning 7+ days including the 526,651-row spike) landed between
  01:33 and 02:03 UTC. Anchoring window boundaries at 03:00 UTC clears that range
  with nearly an hour of margin, preventing the exact split-cluster artifact that
  corrupted this investigation's first (midnight-cut) 7-day count. This applies to
  how C1.4 computes calendar-window boundaries for backfill pagination chunking; the
  persistent watermark value itself is not calendar-bucketed and is unaffected.

### `:updated_at` (and other fields) are silently omitted without an explicit `$select`

- **Discovered:** while building `ingest/schema.py`/`ingest/socrata_client.py`, a
  test parse of a bare `$limit`-only query (no `$select`) showed `updated_at: None`
  for every row. Checking the raw response confirmed `:updated_at` is simply absent
  from the JSON keys when `$select` isn't specified — not null, not erroring,
  **absent**. The same is true for several dataset columns not in Socrata's default
  field set (e.g. `due_date`).
- **Evidence:** live comparison of a default-shape response's key list against the
  C1.2 authoritative 48+1 column list.
- **Downstream effect:** every production request in `ingest/socrata_client.py`
  (`fetch_page`/`paginate`) now requires an explicit `select_clause` argument,
  sourced from `ingest/schema.py`'s `select_clause()` (built from the same
  authoritative column list as the bronze schema, so it can't drift out of sync).
  This is exactly the kind of silent failure mode the project's standing rules warn
  about — a pipeline that "ran successfully" while writing an always-null watermark
  column would be far worse than one that errors loudly.

### `$where` and `$order` on different fields times out server-side — backfill and incremental use different field pairings

- **Discovered:** while building `ingest/pipeline.py`, testing phase-1.md's literal
  C1.4 instruction ("order by the watermark field, then unique_key as tiebreaker")
  applied to a `created_date`-bounded backfill window — i.e. `$where` on
  `created_date`, `$order` on `:updated_at,unique_key` — the request **did not
  return within two consecutive 60-second timeouts**, then eventually succeeded on
  a later retry (total elapsed: several minutes, exact figure not captured since it
  ran as an unattended background probe). Same-field combinations, tested
  immediately after as a control, both returned in under a second: `$where`+`$order`
  both on `created_date` → 0.34s; both on `:updated_at` → 0.61s.
- **Evidence:** live timing comparison, 2026-08-20. The mixed-field query
  eventually returned correct, correctly-sorted results — so this is not a hard
  failure — but at 100-1000x the latency of the same-field combination, which is
  unusable inside a normal per-request retry budget and would make backfill
  windowing on this pairing impractical regardless of correctness.
- **Root cause (inferred, not confirmed by Socrata docs):** Socrata's backend can
  almost certainly use an index to satisfy a `$where`+`$order` pair on the *same*
  column, but has no index that serves an arbitrary filter-on-X / sort-on-Y
  combination — it likely falls back to scanning and sorting a much larger
  candidate set (or the whole table) before applying `$limit`, which times out on a
  22M-row dataset.
- **Downstream effect — design correction:** phase-1.md's ordering instruction is
  applied **only where the chunking field and the order field are the same field**:
  - **Backfill** (one-time historical load, chunked by `created_date` per C1.4):
    `$where` **and** `$order` both on `created_date,unique_key` — reusing Phase
    0's already-proven-fast, already-proven-stable pattern. `:updated_at` is still
    read as a selected column on every row (cheap — it's not filtered or sorted
    on), and the maximum `:updated_at` observed across the entire backfill seeds
    the initial watermark for the first incremental run.
  - **Incremental runs** (post-backfill, chunked by `:updated_at` per the approved
    watermark strategy): `$where` **and** `$order` both on `:updated_at,unique_key`
    — this is the combination C1.1 originally proved fast and stable, and it's the
    only place `:updated_at` needs to be both filtered and sorted on.
  - The watermark strategy itself, the 48-hour buffer, and the 03:00 UTC boundary
    anchor (both from the H1.1-conditioned decisions) are unaffected — this changes
    only how backfill windows are queried, not what field the incremental
    watermark is stored or compared in.

### C1.4/STOP GATE 1 criterion 6 — deliberate kill mid-window, proven on real data

- **Test:** ran a real 2-month backfill (2026-05, 2026-06) in the background. After
  window `2026-05` completed (331,978 rows, checkpoint `status=complete`), the
  process was `kill -9`'d partway through window `2026-06` — confirmed mid-window
  by checking the checkpoint file immediately after the kill, which showed
  `2026-06: {status: "in_progress"}` with no `rows_fetched`/`completed_at`, while
  bronze already held one partially-written page (50,000 rows) from that
  in-progress window.
- **Resume:** re-invoked `run_backfill` over the same range. Log:
  `Window 2026-05 already complete, skipping` (0 rows re-fetched) →
  `Window 2026-06: 334,833 rows` (re-run in full from scratch, since it was never
  marked complete).
- **Result:** final bronze row count **666,811 = 331,978 + 334,833 exactly** — the
  50,000-row fragment written before the kill was silently absorbed into the
  re-run's upsert with **zero duplication** (idempotent MERGE on `unique_key`).
  Both windows' `count_matched: true` against their `$select=count(*)` bound.
- **Consequence:** criterion 6 (checkpoint/resume) is proven on real data, not a
  synthetic scenario, and it incidentally also reinforces criterion 9 (MERGE
  idempotency) — re-processing a partially-written window produced no duplicate
  rows. **2026-05 and 2026-06 are genuinely, correctly backfilled** as a byproduct
  of this test; the full-scale backfill (C1.6) will skip them via the same
  checkpoint mechanism rather than waste the work.

### Backfill-to-incremental watermark handoff (caught in human review before C1.6)

- **The bug caught:** `run_backfill`'s original implementation seeded the first
  incremental watermark from the **maximum** `:updated_at` observed anywhere in the
  backfilled data. That trends toward "now" (the most recent batch cycle touches
  rows across the whole created_date range, as seen directly in the May/June test:
  both months' `watermark_high` came back `2026-08-19T01:33:23` — the same-day
  spike timestamp, despite the underlying rows being 2-3 months old by
  `created_date`). Seeding from that value would strand any row whose
  created_date-window was already pulled and committed **before** a
  concurrent-with-backfill change happened to it elsewhere in the live source —
  exactly the 23.37%-still-open cohort from C1.1, if any of them close mid-backfill.
- **Why max is wrong, precisely:** the backfill takes ~75-90 minutes wall-clock
  (measured from the 2-month test: ~3.4 min/month-window × ~22 remaining months).
  During that span, a row created in an early-processed window (e.g. `created_date`
  in 2024-09, pulled in the first few minutes) could close at any point in the
  *remaining* ~85 minutes. Its bronze copy would be stale (still Open) because that
  window was already committed. The next batch cycle will eventually stamp its
  `:updated_at` to reflect the close — but that stamp's exact value isn't
  predictable from the data itself, so bounding the incremental watermark by
  anything derived from *observed* `:updated_at` values (max, or a fixed offset
  from it) doesn't reliably predate every such in-flight change.
- **The fix:** seed the first incremental watermark from **the earliest `started_at`
  timestamp across every checkpointed window** (this run's and any prior run's —
  correctly picks up the 2026-05/06 kill/resume test's earlier start too), **minus
  the 48-hour buffer**. This is the earliest wall-clock moment at which any
  currently-committed window's data could have gone stale relative to a live-source
  change — any change from that point onward could have raced against whichever
  window happened to be in flight, so re-scanning from there (with the standard
  buffer) is the correct safety floor. `ingest/pipeline.py`'s `run_backfill` now
  computes this directly from the checkpoint file rather than tracking any
  data-derived `:updated_at` value.
- **Cost of the fix:** bounded and small, not the full-table re-pull a maximally
  conservative rule (seeding from the *minimum* observed `:updated_at`, which could
  reach back to the start of the 24-month window) would cost. At the measured
  steady-state baseline (~11,905 rows/day ≈ 0.16% of the table/day), a ~90-minute
  backfill span plus the 48h buffer implies a first-incremental-run re-scan on the
  order of ~2 days' worth of touched rows, roughly 20,000-25,000 rows — all
  absorbed as no-op writes for anything unchanged (C1.3b), so the real cost is
  bounded read/scan time, not write amplification.
- **Not adopted:** seeding from the minimum observed `:updated_at` across the
  backfill (the alternative raised in review) — correct and safe, but would
  re-scan close to the entire 24-month table on the first incremental run, far more
  conservative than the race window actually requires.

### Bug found via C1.9: incremental runs weren't scoped to the backfill window — fixed and purged

- **Discovered:** C1.9's DQ-by-year probe (run against the real bronze table via
  DuckDB) showed rows in years 2020-2023, despite the backfill being strictly
  scoped to `created_date >= 2024-08-19`. Investigation traced this to
  `run_incremental`'s query: `:updated_at >= watermark - buffer` has **no
  `created_date` bound at all**, so it pulls any row from the *entire 22.2M-row
  source dataset* that gets touched by a batch/republish cycle — not just rows
  inside the chosen 24-month window. A same-day republish event (the same one
  behind C1.7's large fetch volumes) touched 4,340 rows with `created_date` back
  to 2020-01-01, all sharing one exact `:updated_at` stamp
  (`2026-08-20T01:33:14.813Z`), and the incremental pipeline correctly-per-its-own-
  logic inserted all of them into bronze as "new" rows.
- **This directly re-explains C1.7's earlier numbers:** the "+4,340 net row
  growth" reported for the second incremental run was **not** genuine new 2024+
  activity as originally assumed while reporting it live — it was entirely this
  scope leak. Corrected below.
- **Evidence:** `table.scan(row_filter="created_date < '2024-08-19T00:00:00'")`
  returned exactly 4,340 rows, all with the same `:updated_at` stamp. Confirmed
  the C1.8 proof row (`unique_key=68857791`, `created_date=2026-05-02`) was
  unaffected — safely in-scope.
- **Fix:** added `created_date >= '{BACKFILL_START}'` as an additional `AND`
  clause in `run_incremental`'s `$where`. Verified this doesn't reintroduce the
  earlier mixed-field where/order timeout (it's an *additional* filter on the
  same field pairing that's already fast — `:updated_at` remains both the primary
  filter and the `$order` field; `created_date` is a secondary narrowing
  condition, not a competing sort target): live test, 0.31s, no regression.
- **Cleanup:** purged the 4,340 out-of-scope rows via `table.delete(delete_filter=
  "created_date < '2024-08-19T00:00:00'")`. Bronze row count returned to
  **7,522,072** — exactly matching C1.6's original correct backfill total.
- **Re-verified:** ran the incremental pipeline again with the fix in place:
  546,445 rows fetched (still large, dominated by the same republish-window
  overlap as before — expected, unrelated to this bug), **0 rows updated, 0 rows
  inserted, 0 stray out-of-scope rows, row count and snapshot count both
  unchanged.** This is now the authoritative "clean" incremental-run measurement,
  superseding the earlier contaminated figures for STOP GATE 1 reporting.
- **Why this is worth reporting prominently, not quietly patching:** per CLAUDE.md's
  standing rule to report failures as an asset — this is a genuine architecture gap
  in the original C1.4 design (phase-1.md didn't anticipate that an
  `:updated_at`-only incremental filter would need a `created_date` guard), caught
  by cross-checking DQ output against expectations rather than trusting a "the
  pipeline ran successfully" result at face value.

### C1.5 — partition-scoped upsert: one mechanism, two measured consequences

**Corrected framing (per Gate 1 review):** the 38.8x scan speedup and the
147.5x/11,419x write amplification (measured later, see the write-amplification
entry below) are not two separate findings — they are the **same mechanism**
viewed from its two sides. `scoped_upsert` ANDs a `created_date` partition-range
predicate into the match scan. That predicate makes the **read/scan** side fast
by pruning partitions before `unique_key` is ever evaluated (a real 38.8x on the
full-scale table). But the underlying primitive it feeds — `table.overwrite()`
— **rewrites at whole-partition granularity regardless of how few rows in that
partition actually matched**: touching any partition, for any reason, causes
every file in it to be deleted and rewritten. Narrowing the predicate can only
ever help decide *which* partitions get touched — it cannot change *how much*
of a touched partition gets rewritten, because that's fixed at "all of it."

**Why backfill escapes the amplification side of this and incremental doesn't:**
backfill batches are `created_date`-chunked, so a touched partition's rewrite is
mostly *rows that were going to be written into that partition anyway* — write
volume ≈ genuine content, amplification is invisible. Incremental batches are
`:updated_at`-chunked, so `created_date` scatters across the full 24-month
range within one batch; a handful of genuinely changed rows can each land in a
*different* partition, and each of those partitions gets fully rewritten for
that handful — write volume ≫ genuine content. Same mechanism, opposite
outcome, entirely explained by which field the batch happens to be ordered/
chunked on.

- **Read-side measurement (scan speedup):** a real 500-row no-op batch (all
  `created_date=2025-03-15`, already correctly present) against the full-scale
  table (7,522,072 rows, 170 snapshots, 869 files, ~730 partitions): PyIceberg's
  plain `table.upsert()` (unscoped, `unique_key IN (...)` only) took 3.37s;
  `bronze.scoped_upsert()` (partition-range ANDed in) took 0.09s —
  **38.8x faster**, because the unscoped scan must consider manifests across all
  ~730 partitions while the scoped scan prunes to the 1 touched partition before
  evaluating `unique_key` at all.
- **Write-side measurement (amplification):** see the dedicated entry below —
  147.5x and 11,419x rewrite amplification, measured on real production pages
  from run 2, both driven by the same whole-partition-rewrite behavior.
- **Consequence:** the predicate-narrowing optimization is real and large, but it
  only ever pays off on the read side. Whether it's a net win depends entirely on
  how concentrated the batch's `created_date` values are — tight for backfill
  (wins big), scattered for incremental (can lose big). See the sub-chunking
  measurement below for whether the write-side cost is avoidable.

### Gate 1 human review: three corrections, resolved with real evidence

Human review of the STOP GATE 1 report caught three problems. Investigated each
against the actual Iceberg snapshot history and raw-landing file timestamps
(ground truth, not reconstructed memory) rather than just accepting or restating
the original claims.

**Correction 1 — Criterion 7 is NOT proven; marked PARTIAL.** The watermark was
seeded to `2026-08-18T01:22:36` with a 48h buffer, and the backfill ran through
`2026-08-20T04:53:07` — so the first incremental query's window
(`2026-08-16T01:22:36` to the `03:00`-anchored boundary) was a window the backfill
had *already fully covered*. A 0-genuinely-new-row result was close to guaranteed
by construction, not demonstrated against real new activity. The insert code path
on the incremental route (different ordering field, chunking, and query
construction from the backfill route) has only ever executed once — during run
2, and only against the 4,340 out-of-scope stray rows (a bug artifact, not
genuine new 2024+ activity; see the scope-leak entry above). **It has never been
exercised against a genuinely new, in-scope row.** Recorded as PARTIAL in
`docs/metrics.md`, both results kept side by side, not overwritten: idempotency
proven (run 4: 0 updated, 0 inserted, 0 net growth, 100% no-op), insert path
unproven. Re-test scheduled for ≥24h after the C1.6 backfill completed
(2026-08-20T04:53:07 UTC), i.e. no earlier than 2026-08-21T04:53 UTC.

**Correction 2 — criterion 11's snapshots, precisely identified.** The reported
"real row-count difference via time travel" compared snapshot #163 (the last
snapshot of the C1.6 backfill, `snapshot_id=4000161280114858559`, 7,522,072 rows,
committed 2026-08-20T04:53:06.917Z) against what was then the *current* snapshot
at the time the C1.8 script ran — snapshot #169 (`snapshot_id=2132395397023993280`,
7,526,412 rows, committed 2026-08-20T05:12:05.148Z), i.e. **immediately after the
buggy pre-fix incremental run 2, before the purge.** The difference (+4,340) is
**entirely the scope-leak bug's insertion**, not genuine new activity — confirmed
by the exact match to the bug's row count. This was a real, valid snapshot
comparison (time travel correctly reflects a genuine — if buggy — committed
write), but presenting it without stating which snapshots were compared implied
it was evidence of legitimate incremental growth, which it was not. **A cleaner,
currently-valid alternative exists in the same snapshot history**: snapshot #169
(7,526,412, pre-purge) vs. snapshot #170 (`snapshot_id=3746702539029603816`,
7,522,072, the `DELETE` operation that purged the 4,340 stray rows,
2026-08-20T05:36:29.379Z) — a real, inspectable, **currently still valid** −4,340
time-travel difference produced by the bug fix itself, arguably more interesting
than an incremental-growth demo since it demonstrates time travel capturing a
correction, not just an insertion. Going forward, criterion 11's authoritative
evidence is **this delete-snapshot pair**, not the original insert-snapshot pair.

**Correction 3 — the 546,445 finding, promoted to a real measurement.** See the
dedicated entry below.

**Snapshot accounting, 163 → 170, verified against the actual manifest history**
(not reconstructed from memory — audited via `table.snapshots()` summaries and
cross-referenced against `raw/` file mtimes):

| Snapshots | Operation | Row delta | Attributed to |
|---|---|---|---|
| #164 | APPEND +234 | +234 | Run 2, page 1 of 12 (partial genuine matches within that page's key set) |
| #165 | APPEND +4,106 | +4,106 | Run 2, a later page |
| #166 | OVERWRITE +662,291 / −666,811 | −4,520 | Run 2 — see anomaly entry below |
| #167 | APPEND +4,520 | +4,520 | Run 2, compensating append immediately after #166 |
| #168 | OVERWRITE +11,418 / −11,419 | −1 | Run 2 — same anomaly, smaller instance |
| #169 | APPEND +1 | +1 | Run 2, compensating append immediately after #168 |
| #170 | DELETE −4,340 | −4,340 | The scope-leak purge |

Sum of #164-#169: 234+4,106−4,520+4,520−1+1 = **+4,340**, exactly matching run 2's
reported net growth. All 6 of these snapshots fall within run 2's actual
wall-clock execution window (verified via `raw/` parquet file mtimes,
2026-08-20T04:56:16Z–05:12:14Z) — **run 3 and run 4 (the post-fix run) each
genuinely added zero snapshots**, matching what was originally reported for them.
The only correction needed was attributing all 6 of run 2's snapshots correctly
and understanding their *composition*, which was previously reported only as an
aggregate "+6" without this detail.

### Unexplained scoped_upsert anomaly during run 2 (verified harmless, root cause not fully confirmed)

- **Discovered:** while auditing the snapshot history above, found two `OVERWRITE`
  operations mid-run-2 that don't fit a simple mental model of `scoped_upsert`:
  one replaced 666,811 existing rows with 662,291 rows in a single call, the other
  replaced 11,419 with 11,418. Both were immediately followed (same or next
  second) by a compensating `APPEND` restoring the missing count exactly (+4,520,
  +1 respectively) — net zero across each pair.
- **Why this is surprising:** `scoped_upsert`'s `rows_to_update` (the argument to
  `table.overwrite()`) is derived via `upsert_util.get_rows_to_update(page,
  existing, [join_col])`, which returns a subset of the *incoming page* — bounded
  by the page size (≤50,000 rows, per `PAGE_SIZE`). A single page cannot logically
  produce a 662,291-row `rows_to_update`. The likely trigger: run 2 is
  `:updated_at`-chunked, so a single 50,000-row page's `created_date` values are
  scattered across the full 24-month range rather than clustered — if even one or
  two rows in a page fall in, say, May and June 2026, `partitions_touched()`
  returns `min(days)..max(days)` spanning that entire range, and
  `scoped_upsert`'s partition predicate widens to match. Why that would cause
  `table.overwrite()` to report replacing 666,811 rows specifically (matching
  the exact size of the May+June 2026 partitions) rather than just the actual
  matched subset is **not fully understood** — a plausible but unconfirmed theory
  is that PyIceberg's `overwrite()` resolves the delete side of the operation at
  file granularity once `overwrite_filter`'s partition component is wide enough to
  select those files, rather than purely by the row-level match predicate, and
  the compensating append is `scoped_upsert`'s own not-matched-insert logic
  correctly re-inserting whatever the overwrite didn't restore.
- **Verified NOT a data-integrity problem, with real evidence:** May 2026 row
  count = 331,978 (exact match to the known-correct kill/resume-test total), June
  2026 = 334,833 (exact match), **zero duplicate `unique_key` values anywhere in
  the 7,522,072-row table**, and an independent DuckDB `iceberg_scan` cross-check
  matches PyIceberg's row count exactly. Whatever this mechanism is, it net out
  to the correct final state both times it occurred.
- **Consequence:** flagged as an open item, not resolved. `scoped_upsert`'s
  behavior on `:updated_at`-chunked batches (where the partition-range predicate
  can be arbitrarily wide relative to the actual matched-row count) should not be
  fully trusted at larger scale without understanding this mechanism — it
  happened to self-correct via the immediate compensating append both times
  observed, but "happened to self-correct" is not the same as "verified correct
  by design." Worth a deeper look before Phase 2, or before scaling incremental
  run frequency/volume, even though it isn't blocking Gate 1 (data is verified
  correct today).

### C1.7d — the 546,445-row finding, with full arithmetic (promoted per Gate 1 review)

- **The measurement:** the authoritative post-fix incremental run (run 4) queried
  `:updated_at` from `2026-08-18T02:23:09.123Z` (the watermark then in effect,
  itself already advanced by run 2/3, minus the 48h buffer) to
  `2026-08-20T03:00:00Z` (the `03:00`-anchored boundary) — a span of **2 days,
  0:36:51 ≈ 2.0256 days.**
- **Expected volume at steady state:** ~11,905 rows/day (C1.1b's measured
  baseline, excluding the known republish-spike outlier) × 2.0256 days ≈
  **24,115 rows.**
- **Observed volume:** **546,445 rows** — a ratio of **546,445 / 24,115 ≈ 22.7x**
  expected steady-state volume for that window.
- **No-op rate:** **100.00%** (0 rows updated, 0 rows inserted, 546,445 no-op) —
  every single one of those 546,445 fetched rows was a `:updated_at` advance with
  zero material field change relative to what bronze already held.
- **Operational consequence:** every incremental run pays a real, sometimes
  20x+-inflated **fetch/network cost** to apply what may be a small or zero number
  of genuine changes, because the 48h buffer window overlaps the known Aug-19
  republish event (a single-day, ~526,000-row batch touch, per C1.1b).
- **Does this validate or undermine option (ii) (the pre-upsert diff-filter
  design)?** **Validates the "build nothing" conclusion (C1.3b)**, but narrows
  what it actually claims. The 22.7x volume inflation is entirely a **fetch-side**
  cost — the API must be queried and the rows must be downloaded regardless of
  what happens locally afterward, since Socrata's `:updated_at` field itself
  advances broadly during a republish event and there is no way to distinguish
  "genuinely changed" from "republish-touched" without fetching and comparing
  values. A hand-rolled diff-filter would only ever reduce **local write** cost —
  and C1.3b/this run both confirm that cost is already zero for no-op rows via
  PyIceberg's built-in behavior. So: the decision to not build a diff-filter is
  still correct (it wouldn't have helped with this specific cost), but it should
  not be read as "incremental runs are cheap" — they can be fetch-expensive during
  a republish window regardless of any local optimization.
- **Honesty caveat, as instructed:** this is **one observation**, and the window
  it covers directly overlaps the known Aug-19 republish spike — it may not
  reflect steady-state incremental cost at all. This measurement will be repeated
  on the criterion-7 re-test (≥24h after the backfill) and both results reported
  side by side, not averaged or replaced.

### Investigation: is the overwrite anomaly partition-level rewrite, and is it atomic? (Gate 1 follow-up)

Human review correctly rejected my initial "widened predicate over scattered
rows" theory: 666,811 is exactly the May+June 2026 backfill total — whole
partitions being removed, not a row-count coincidence. Investigated per the
five-step plan given, using real evidence at each step rather than continuing to
guess.

**1. Manifest audit — were ALL files in the touched partitions deleted, or a
subset?** Printed the full snapshot summary properties (not just the
top-level counts) for both anomalous snapshots:
- Big overwrite: `added-data-files=73, deleted-data-files=73,
  changed-partition-count=61, deleted-records=666811, added-records=662291`.
  **61 = exactly the number of days in May+June 2026 (31+30).** Every file in
  every one of those 61 partitions was rewritten.
- Small overwrite: `added-data-files=1, deleted-data-files=1,
  changed-partition-count=1, deleted-records=11419, added-records=11418`. One
  partition (one day), its one file, fully rewritten.

**2. Isolated reproduction — one row, one partition.** Built a 3-partition
throwaway table (100 rows/day), then called `scoped_upsert` with a 2-row batch
spanning day1 and day3 (skipping day2), where only 1 row (day1) was a genuine
change and the other (day3) was byte-identical (no-op). Result: **day1's single
100-row file was entirely deleted and rewritten with 99 rows, then a separate
append added the 1 changed row back — net 100, correct.** Day2 (never in the
batch) was untouched (100 rows, verified). Day3's file was never touched at
all (its row was a no-op, correctly excluded from `rows_to_update`, so its file
was never a delete/rewrite target). **This confirms: partition-level rewrite is
triggered per-partition, only for partitions that actually contain a genuinely
changed row — not swept broadly across the full min/max date span.** (My
earlier "widened predicate" theory was wrong for a different reason than
originally stated — it's not that unrelated partitions get swept in; it's that
*any* partition containing even one changed row gets its *entire* file set
rewritten, discarding nothing but reconstructing the whole thing.)

**Mechanism, now fully understood by reading `Transaction.overwrite()`'s
source:** it calls `self.delete(delete_filter=overwrite_filter, ...)` — Iceberg's
delete resolves to a copy-on-write file rewrite when the filter can't be
satisfied by whole-file drops, producing one snapshot (`OVERWRITE`: the touched
file(s) minus the matched rows) — then, in a **separate** step, appends the
caller-supplied replacement data (`df` = `rows_to_update`) as a second snapshot
(`APPEND`). Both showed up as **two distinct snapshot IDs** in every case
observed. Re-deriving the real bronze numbers with this understood: big case —
`rows_to_update` was **4,520** rows (666,811 − 662,291, matching the
compensating append's `+4,520` exactly); small case — `rows_to_update` was
**1** row (matching its compensating append's `+1` exactly). Both fully
consistent and now fully explained — no remaining mystery.

**3. Write amplification, quantified from real production data (no synthetic
benchmark needed — these are actual run-2 pages):**

| Case | Rows genuinely changed | Rows rewritten (deleted+recreated) | Amplification |
|---|---|---|---|
| Big overwrite | 4,520 | 666,811 | **~147.5x** |
| Small overwrite | 1 | 11,419 | **~11,419x** |

This is severe, and it is specific to **`:updated_at`-chunked batches**, whose
`created_date` values scatter across the full 24-month range within a single
page — every genuinely-changed row can trigger a full rewrite of whatever
partition it lands in, and if a batch happens to touch a changed row in each of
61 different partitions, all 61 partitions get fully rewritten. This did NOT
show up as a problem for **backfill** (`created_date`-chunked, one page's
partition-range is always narrow — usually one or a few adjacent days), which
is exactly where the 38.8x scoped-vs-unscoped speedup was measured. **The two
regimes have opposite characteristics: `scoped_upsert`'s partition-scoping helps
enormously for backfill and can badly hurt for incremental.** Likely explains
why the incremental runs (589,388/550,567/546,445 rows fetched, only ~11-12
pages each) took 900-1040 seconds — write cost from these amplified rewrites,
not fetch time, plausibly dominates.

**4-5. Durability — is the delete+append atomic, tested with a real kill.**
Reproduced the exact scenario (1 changed row, 1 partition, 100-row file) on a
fresh throwaway table, monkey-patching `Transaction.delete` to sleep 10 seconds
immediately after it returns — i.e., right between the internal delete-rewrite
commit and the internal append commit. Waited for a log marker confirming the
delete step had run, then `kill -9`'d the process. **Result: the table showed
exactly 1 snapshot afterward — the original pre-operation state. Zero rows
lost, zero partial commit, id=0 still read "orig" (its pre-change value).**
Ran the identical scenario to completion (no kill) as a control: it correctly
produced 2 snapshots (OVERWRITE −100/+99, APPEND +1) and the final state was
fully correct (100 rows, id=0 = "CHANGED").

**Conclusion: `table.overwrite()` — and therefore `scoped_upsert` — IS ATOMIC.**
Despite committing as two separate snapshots when successful, a crash anywhere
during the call leaves the table in its exact pre-call state; there is no
partially-committed, data-losing intermediate state reachable by a crash.
**Per the pre-agreed framing: this is a documented write-amplification
characteristic with a number attached, not an architecture-blocking bug.**
Phase 2 does not need to wait for a fix here — but the write-amplification
finding above means `scoped_upsert` should likely not be used as-is for the
incremental path without reconsidering the chunking strategy (e.g., grouping
an incremental batch by `created_date` sub-ranges before upserting, or falling
back to plain unscoped `table.upsert()` for the incremental route specifically,
since its match-predicate-only file selection doesn't provoke this
partition-wide rewrite the same way). Left as a documented open consideration
for Phase 2, not resolved here — the correctness and durability questions are
answered; the performance-strategy question is not.

**Partition-level row-count assertion — added, per instruction.** `bronze.py`'s
`scoped_upsert()` now captures the touched partitions' total row count
immediately before any write, and asserts (after both the overwrite and insert
steps) that the post-write count equals `before + rows_inserted` exactly,
raising loudly on any mismatch. This is the check that would have caught the
original anomaly directly, without needing a manifest audit — verified against
a legitimate update+insert case (200→201 rows, no false alarm).

### Phase 2 strategy question, resolved by measurement: sub-chunking does NOT help

Tested the hypothesis directly on the real bronze table, per instruction, rather
than reasoning further from the isolated toy reproduction.

**First attempt was confounded — caught before drawing a conclusion from it.**
The first version of this test reused the *same* 60 rows across all three
conditions (unscoped → mutate, scoped-bundled → mutate again, scoped-sub-chunked
→ revert). Result looked dramatic — scoped-bundled and sub-chunked both showed
**zero amplification** (60 rows rewritten for 60 changed) — but this was an
artifact: the *first* test (unscoped) had already rewritten those partitions,
splitting each touched row into its own small file via `overwrite()`'s
append step. The second and third tests were then updating rows already
isolated in tiny files, not fresh partitions — a fundamentally different, much
cheaper case than the real production anomaly, which hit partitions untouched
since the original backfill. Re-ran with three **disjoint** sets of 30 days
each (90 distinct days total, verified zero overlap), so every test hits
genuinely fresh, never-touched-since-backfill partitions — matching the real
scenario.

**Corrected results — 60 genuinely changed rows, scattered across 30 fresh
partitions, for each of three strategies:**

| Strategy | Elapsed | Rows rewritten | Amplification |
|---|---|---|---|
| Unscoped `table.upsert()`, bundled | 7.118s | 254,412 | 4,240x |
| Scoped `scoped_upsert()`, bundled (as-is) | 8.729s | 260,168 | 4,336x |
| Scoped `scoped_upsert()`, sub-chunked by `created_date` (30 separate calls) | 28.824s | 231,833 | 3,864x |

**Sub-chunking does not help — reported plainly, per instruction.** The
amplification is essentially identical across all three strategies (4,240x /
4,336x / 3,864x — the ~11% spread is consistent with ordinary day-to-day
partition-size variance across three different calendar periods, not a
strategy effect). Sub-chunking is additionally **3-4x slower in wall-clock**
(28.8s vs. 7-9s), because splitting one bundled call into 30 separate
`table.overwrite()` calls multiplies per-call transaction/commit overhead
(30×2=60 new snapshots vs. 2) without buying back any rewrite savings.

**Why the hypothesis was wrong:** amplification is fixed **per touched
partition**, not per batch-construction strategy. Whether 60 scattered
changed rows arrive as one bundled call, one call per day, or an unscoped
call, each of the 30 touched (fresh, never-modified) partitions still needs
its entire file rewritten once *something* in it changes — that cost is
determined by "was this partition ever touched before," not by "how was the
incoming batch grouped when it was touched." Sub-chunking cannot reduce it
because it doesn't change *which* partitions get touched, only how many
separate transactions do the touching (which only adds overhead).

**Unscoped is not worse here either — also reported plainly.** For this
kind of large-amplification batch, unscoped `table.upsert()` was
*slightly faster* than scoped (7.1s vs. 8.7s) and had comparable rewrite
volume. This makes sense in light of the unified C1.5 framing above: scoping's
benefit is entirely on the read/scan side, and when write cost from
unavoidable partition rewrites dominates (as it does here), the scan-side
saving becomes a rounding error next to it.

**What would actually help (not measured, out of scope here):** the
amplification is inherent to Iceberg's copy-on-write semantics for
`overwrite()` — an update to any row in an untouched partition forces a full
file rewrite regardless of how the calling code batches its requests. A real
fix would need to change the *write mode* itself — e.g. merge-on-read
(positional/equality delete files instead of file rewrites, the same mode
Athena's Iceberg support already assumes per CLAUDE.md's known traps) — not
how `scoped_upsert` groups its input. Left as an open question for whoever
picks up the incremental-write-strategy work next; not resolved or adopted
here, since the measured evidence doesn't support adopting sub-chunking and no
alternative was implemented or tested.

**Bronze integrity after testing:** all 238 distinct `unique_key`s touched
across both the confounded and corrected experiments were reverted to their
original `descriptor` values via a final scoped upsert. Verified: 0 mismatches
against original values, total row count exactly 7,522,072 (unchanged).

### README-bound finding (flagged so it isn't lost before Phase 7)

**"Partition-scoped upsert rewrote 11,419 rows to change 1"** — the mechanism
(copy-on-write `overwrite()` rewrites entire touched partitions regardless of
how few rows match), the measured numbers (147.5x and 11,419x amplification in
production; 4,240x-4,336x reproduced and quantified on a controlled 60-row/
30-partition test; 38.8x scan speedup as the other side of the same
mechanism), the durability finding (proven atomic via a real kill-mid-operation
test — no data loss, full rollback), and the negative result (sub-chunking
doesn't help, tested and quantified) together are the strongest engineering
narrative in the project so far: a real anomaly, root-caused from first
principles (source code reading + isolated reproduction), quantified, proven
safe, and an intuitive mitigation tested and honestly reported as not working.
**Phase 7's README must include this finding, with the mechanism, the numbers,
and the durability proof** — not just the headline number.

### Criterion-7 re-test attempt 1: same-window repeat, not a design gap

Ran `run_incremental()` at 2026-08-20T17:33 UTC (~12.5h after the backfill, short
of the originally-stated 24h threshold). Result was **bit-for-bit identical** to
the prior post-fix run: 546,445 fetched, 0 updated, 0 inserted, 100% no-op, 0 row/
snapshot delta. Traced the cause: `run_incremental`'s `query_end =
_anchor_boundary(now)` rounds down to the most recent `03:00` UTC boundary. Since
"now" hadn't crossed into `2026-08-21T03:00Z` yet, `query_end` resolved to the
exact same `2026-08-20T03:00Z` as the previous run, and the watermark hadn't
moved either (nothing to advance it to) — so the query window was identical, not
just similarly-shaped.

**This is correct behavior, not a bug — corrected per human review after an
initial mischaracterization.** Boundary anchoring exists specifically so
incremental windows align to the *source's actual publish cycle* (the observed
~daily batch stamp, ~01:33-02:03 UTC) rather than to an arbitrary wall-clock
duration. An identical same-day window is the **expected, correct** outcome
when no new publish cycle has occurred since the last run — the mechanism is
working as designed, not failing.

**The real lesson is about the re-test threshold, not the code:** the
originally-stated "≥24h after the backfill" was the wrong criterion. **The
correct threshold is "after the next publish cycle" — i.e., after
`2026-08-21T03:00Z`, whenever that specific boundary is next crossed — not a
fixed 24-hour clock duration.** This distinction matters beyond this one
re-test: **it should govern how Phase 6's cron schedule is set.** A schedule
that fires on a fixed interval without regard to the publish-cycle boundary
risks the same outcome recurring in production — a scheduled run that
queries an already-fully-covered window, burns the fetch cost, and reports
"0 new rows" in a way indistinguishable from "the watermark mechanism is
broken." Phase 6 should schedule incremental runs to trigger *after* the
`03:00` UTC boundary each day (with some margin, e.g. `03:15`), not on an
arbitrary cadence relative to when the previous run happened to execute.

### PROPOSAL (not implemented, awaiting approval) — no-op short-circuit for same-window runs

Investigated per instruction; **not implemented in code**. Design:

Before `run_incremental()` does anything expensive (the `count()` call and the
page-fetch loop), compare the freshly-computed `(query_start, query_end)`
against the `start`/`end` recorded on the most recent completed
`incremental_*` checkpoint entry. If both match exactly, log
`"No new publish cycle since last run (window unchanged: [{query_start},
{query_end})) — skipping fetch."` and return a zero-cost result immediately
(`rows_fetched=0`, etc.) without calling `count()` or `paginate()` at all.

**Why this is safe:** `query_start` is derived from the stored watermark (which
only ever advances forward when genuine new activity is found) and
`query_end` from the `03:00`-anchored boundary (which only advances once a day
crosses that boundary). If *both* are unchanged from the last completed run,
no new data could possibly exist in a query that scans that exact same range
again — Socrata's dataset doesn't retroactively remove `:updated_at` stamps
that were already there. This isn't a heuristic; it's a direct consequence of
the watermark/boundary design already in place.

**Why NOT implemented without approval:** it changes observable behavior (a
same-window re-run currently still performs a real fetch and reconciles
against `count()`, which is itself a form of freshness confirmation — with the
short-circuit, that confirmation goes away for skipped runs). Also interacts
with the checkpoint label collision noted earlier (same-day incremental runs
share one checkpoint entry, overwriting each other) — the short-circuit's
"most recent completed entry" lookup should be checked against that limitation
before relying on it. Worth doing, but a design decision, not an obvious
patch.

### Criterion-7 re-test methodology, extended (per Gate 1 review)

Two additions for the next actual re-test (after `2026-08-21T03:00Z`):
1. **Report watermark before and after**, and confirm it actually advances to
   the value implied by whatever new `:updated_at` maximum is observed. The
   watermark-advance code path (`checkpoint.save_watermark()` inside
   `run_incremental`, guarded by `max_watermark_seen > watermark_dt`) has never
   executed for real — it's been static since the C1.6 backfill seeded it,
   because every run since has found nothing newer. A watermark that silently
   fails to advance would look *identical* to "no new data" indefinitely, and
   nothing so far has distinguished those two cases.
2. Continue reporting the republish-noise ratio against the 22.7x baseline, and
   state plainly whether the new measurement looks like steady state or another
   republish artifact — this is now genuinely a fresh measurement once the
   window actually changes.

### Checkpoint labeling defect — fixed, with the short-circuit built on top

Human review flagged this as its own defect, separate from the short-circuit
approval: `run_incremental`'s checkpoint label was `f"incremental_{query_end
.date().isoformat()}"` — one label per **calendar day**, not per **run**. Any
second same-day invocation (which already happened today — the identical
runs at 2026-08-20T13:33 and the earlier post-fix run) silently overwrote the
prior run's checkpoint record. This destroys run history and — critically —
would have made the short-circuit's "compare against the most recent entry"
lookup read an unreliable, already-overwritten record.

- **Fixed:** `label = f"incremental_{datetime.now(timezone.utc).strftime(
  '%Y%m%dT%H%M%S%f')}"` — timestamped to the microsecond at call time, unique
  per invocation regardless of how many runs happen in one calendar day or
  even one calendar second.
- **Added:** `checkpoint.get_latest_incremental_entry()` — scans all
  `incremental_*`-labeled entries (complete or skipped), returns the one with
  the latest `completed_at`/`skipped_at`/`started_at`, by label prefix so it
  naturally excludes backfill's `YYYY-MM`-labeled entries.
- **Verified by running twice**, per instruction: first call correctly found
  the old pre-fix `incremental_2026-08-20` entry as "most recent," recognized
  an identical window, short-circuited, and wrote a new distinct record
  (`incremental_20260820T185109918200`). Second call found *that* record as
  most recent, also matched, also short-circuited, and wrote a third distinct
  record (`incremental_20260820T185109925821`). **Three distinct entries now
  exist** where before there would have been one, repeatedly overwritten.

### No-op short-circuit — implemented, loud by design

Implemented in `run_incremental`, gated on checkpoint labeling being fixed
first (as instructed). Before any network call: compute `(query_start,
query_end)`, compare against `get_latest_incremental_entry()`. On an exact
match: log at **WARNING** (not INFO, not silent) with the precise repeated
window bounds and the reason, write a checkpoint entry with
`status="skipped_no_new_window"` (its own status, distinguishable from a
completed run that genuinely fetched and found nothing), and return a
zero-cost result — no `count()` call, no `paginate()`, none of the ~546k-row
fetch cost a repeat window would otherwise burn for zero benefit.

**Alerting threshold for Phase 6, reasoned now while the context is fresh:**
**alert on 3 consecutive short-circuits.** Important distinction, recorded so
it isn't conflated later: this is a **scheduling-integrity** signal, not a
**source-staleness** signal, even though both were raised in the same review
comment.
- Under a *correctly*-configured Phase 6 cron (fires once daily, after the
  `03:00` UTC boundary), `query_end` differs every single day by construction
  — the short-circuit should essentially never fire in steady-state
  operation. Any occurrence at all is already slightly unexpected.
- **N=1**: could be an innocuous one-off — a manual re-run, a retry after a
  transient failure in the same cycle. Not alert-worthy alone.
- **N=2**: still plausibly a one-off double-fire.
- **N=3 in a row**: very unlikely to be coincidental — strongly indicates a
  systematic problem (cron interval misconfigured shorter than the publish
  cycle, or a clock/deployment issue preventing `now` from ever crossing the
  boundary). This is the right point to page.
- **What this metric does NOT catch — a separate metric is needed for
  source staleness.** A short-circuit only fires when the window is
  *identical* to before. A run that genuinely fetches on schedule (window
  correctly advanced) but finds zero real inserts is a normal completed run
  with 0 net growth — not a short-circuit, and the counter above stays at 0
  regardless of whether the *source* has actually gone quiet. Detecting that
  (informed by the 2.53-92 day observed publish lag from C1.1) needs its own
  Phase 6 metric — e.g. "days since watermark last advanced due to a genuine
  change" compared against the ~92-day worst-case tail — not derived from the
  short-circuit counter. Flagged here as a distinct Phase 6 requirement, not
  designed or implemented now.

### Criterion 9 headline evidence #2: 4,521 rows genuinely updated by run 2 — why incremental ingestion is necessary, proven concretely

Promoted out of the snapshot-accounting table per Gate 1 review — this is the
clearest demonstration in Phase 1 of the actual problem incremental ingestion
solves, and it deserves its own entry, not a buried aggregate. **Timeline
below is rebuilt from verified UTC sources only** (checkpoint `started_at`/
`completed_at` fields and `snapshot.timestamp_ms`, both genuine UTC in this
codebase) — not from log-file timestamps, which use local system time and
caused a mislabeling earlier in this phase. This corrects the initial
paraphrase of this finding (which conflated the Aug-19 01:33 republish event
with the actual correction-triggering event — close, but the real story is
more precise and, if anything, more compelling):

| Event | UTC time | What happened |
|---|---|---|
| Republish/batch-stamp event | 2026-08-19T01:33:23.553Z | Touches ~526,651 rows dataset-wide (the C1.1b/C1.9 spike). Both May and June 2026 backfill windows later report this as their `watermark_high` — the newest touch visible *as of the backfill pull*. |
| Backfill pulls May 2026 | 2026-08-20T01:22:36 – 01:25:25Z | 331,978 rows landed, reflecting each row's state as of this pull. |
| Backfill pulls June 2026 | 2026-08-20T01:26:26 – 01:30:22Z | 334,833 rows landed, same-day pull, immediately after May. |
| **A same-day (Aug 20) batch cycle touches specific rows with genuine field changes** | sometime in 2026-08-20T01:30:22–03:00:00Z (inferred — falls inside run 2's query window, after the backfill pull, before the window's upper bound) | **This is the race**: the live source changed some May/June rows' content *after* the backfill had already pulled and committed them, and *before* the day's `03:00Z` incremental-window boundary. |
| Run 2 fetches and corrects | 2026-08-20T05:11:18 – 05:12:05Z | Two update pairs: `OVERWRITE` −666,811/+662,291 then `APPEND` +4,520 (net: 4,520 rows genuinely updated in May/June partitions); `OVERWRITE` −11,419/+11,418 then `APPEND` +1 (1 more row, a different single-day partition). **4,520 + 1 = 4,521 rows total, genuinely corrected.** |

**Why this is the clearest proof, not just an accounting curiosity:** the
backfill is `created_date`-chunked and captures each row's state *at the
moment its window is pulled* — it has no way to know if that row changes
again five minutes, or five hours, later, because the backfill's own
execution (~53 minutes wall-clock) is itself a window during which the live
source keeps moving. **This is exactly the concurrent-with-backfill race
condition the watermark-seeding design (earliest window `started_at` minus
the 48h buffer, from the earlier Gate-1-caught correction) was built to
guard against — and here is direct, measured proof it happened for real**,
not a hypothetical. Without the incremental route (and without seeding its
watermark from before the backfill's own start, not from the backfill's
observed data), these 4,521 rows' updated field values — the actual outcome
of whatever changed for those specific 311 requests on 2026-08-20 — would
have been silently and permanently missed. This is criterion 9's headline
evidence, standing alongside `unique_key=68857791`'s Open→Closed proof:
`68857791` shows a single row's correct MERGE update in isolation; this
shows the *systemic reason* the incremental route exists at all, at real
scale (4,521 rows, two separate partitions, a genuine live-source race).

### Snapshot retention and file accumulation — investigated, not implemented

Per instruction: report only. Measured live against the real bronze table.

**(a) Current state.** 300 snapshots. 2,843 physical `.parquet` data files on
disk, 907.6 MB. Of those, only **1,050 files (716.3 MB) are referenced by the
current snapshot** — the other **1,793 files (191.3 MB) are superseded**:
physically present, but not part of any live query, kept only because nothing
has ever removed them. (Sanity-checked: every file the current snapshot
references was confirmed present on disk — 0 missing.) Metadata directory
(manifests, manifest-lists, `metadata.json` history): 1,013 files, 38.1 MB —
also strictly growing, one set per snapshot, forever.

**(b) Disk delta attributable to superseded files since the backfill.** C1.6
measured 731 MB immediately after the backfill (163 snapshots, no test
churn yet). Current total: 907.6 MB data + 38.1 MB metadata = 945.7 MB — a
**+214.7 MB delta**. Of that, **191.3 MB (89%) is superseded data files**;
the remaining ~23 MB is metadata growth plus a small net change in the
current view's own file layout from the amplification/sub-chunking tests.
**Essentially all of Phase 1's post-backfill disk growth is retention debt,
not genuine new data** — genuine new data (the C1.7 net row growth) was 0 by
the time of this measurement.

**(c) Projected growth at one scheduled run/day.** Two framings, both
caveated — there is no real steady-state measurement yet (criterion 7 is
still PARTIAL):
- **Using the one real "genuine changes found" event we have** (run 2's
  4,521-row correction, which rewrote 678,230 rows: 666,811 + 11,419): at the
  current measured **99.9 bytes/row** footprint, that's **~64.6 MB of new
  superseded data per event.** If Phase 6 ran once daily and *every* day
  looked like this one, that's **~23.6 GB/year of pure retention debt** —
  more than 32x the entire current dataset's live size. This is very likely
  an overestimate (run 2's event was a one-time backfill-race correction,
  not demonstrated steady state), but it's the only real number available,
  and it establishes the order of magnitude is not negligible.
- **Bound by partition count instead:** each partition averages
  7,522,072 ÷ ~730 ≈ 10,304 rows. If a normal day's genuine changes touch,
  say, 30-100 distinct partitions (the range explored in the sub-chunking
  test), that's roughly 309K-1.03M rows rewritten/day ≈ 31-103 MB/day —
  broadly consistent with the run-2-based estimate, not contradicting it.
- **Bottom line: without compaction, superseded-file growth is very
  plausibly faster than genuine data growth**, by a wide margin, given the
  measured amplification factors (147.5x-11,419x production, 4,240x-4,336x
  controlled). This needs real Phase 6 operational data to pin down
  precisely, but the order of magnitude is already clear enough to plan
  around now rather than discover in Phase 7.

**(d) What PyIceberg actually supports locally — verified empirically, not
assumed.** `Table.maintenance.expire_snapshots()` exists
(`ExpireSnapshots.by_id()` / `.by_ids()` / `.older_than(dt)` /
`.commit()`). Read its `_commit()` implementation directly: it issues exactly
one `RemoveSnapshotsUpdate` — **it only removes entries from the snapshot
log in `metadata.json`. It does not delete any data file, manifest file, or
manifest-list file from disk.** Searched the entire installed `pyiceberg`
package for `remove_orphan_files`, `rewrite_data_files`, `compact_data`, or
any garbage-collection utility: **none exist.** `MaintenanceTable`'s only
method is `expire_snapshots`. **This directly confirms the instruction's
caution was warranted — Athena's `OPTIMIZE`/`VACUUM` semantics (which do
physically compact and reclaim space) do not apply to local PyIceberg 0.11.1
at all.** Running `expire_snapshots()` locally would shrink the *logical*
snapshot log (and thus how far back time travel reaches) without freeing a
single byte of the 191.3 MB already superseded on disk — actual space
reclamation would require a **hand-rolled orphan-file sweep** (compute the
set of files referenced by any *surviving* snapshot after expiration, diff
against everything on disk, delete the difference) — not provided by the
library, would need to be written from scratch, and was not implemented here
per instruction.

**(e) Proposed retention policy (not implemented) — must preserve criterion
11's evidence.** `ManageSnapshots.create_tag(snapshot_id, name)` exists and
is honored by `expire_snapshots()`: tagged (and branch-HEAD) snapshots are
automatically excluded from any expiration, by ID or by `older_than(dt)` —
confirmed by reading `_get_protected_snapshot_ids()`. Proposed shape, for
Phase 2 or later to actually adopt:
1. **Tag the criterion-11 evidence explicitly** — `#169`
   (`snapshot_id=2132395397023993280`) and `#170`
   (`snapshot_id=3746702539029603816`), e.g. `"phase1-c1.9-pre-purge"` and
   `"phase1-c1.9-post-purge"` — so they survive any future expiration
   regardless of age, by design rather than by accident.
2. **Expire by age for everything else** — e.g. `older_than(now - 30 days)`,
   run periodically (Phase 6 cron, alongside the incremental job) once real
   operational cadence is known.
3. **Add the hand-rolled orphan-file sweep** as its own maintenance step,
   *after* `expire_snapshots()` commits — since (d) confirmed expiration
   alone does not reclaim disk space, a retention policy that stops at step 2
   solves the metadata-log growth but not the actual disk-growth problem
   this investigation was about.
4. Not decided here: the exact age cutoff, sweep frequency, or whether other
   specific snapshots (beyond #169/#170) deserve tags — left for whoever
   implements this, informed by real Phase 6 volume once available.

**Phase 7 consequence, flagged as instructed:** the S3 mirror and Athena
scan-cost planning are sized on **731 MB** (C1.6's post-backfill measurement).
If no compaction/retention policy runs between now and Phase 7, and if
superseded-file growth tracks anywhere near the (c) projections, bronze could
be **many times that size in superseded files alone** by the time it's
mirrored to S3 — meaning both the mirror's storage cost and Athena's
bytes-scanned estimates (if Athena's own Iceberg reads ever touch
non-current files, e.g. during time-travel queries) would be sized on stale
assumptions. A retention/compaction policy should exist and run *before*
Phase 7's mirror, not be designed at mirror time.

### Criterion 7/8/11 final re-test — run after the boundary genuinely passed (2026-08-21)

Confirmed via `date -u` (2026-08-21T14:23:58Z) before running, avoiding the
same-window mistake from the prior attempt. Full numbers in
`docs/metrics.md`'s C1.7 table (run 6 / run 7). Summary:

- **Run 6** (real fetch, `skipped=False`): 559,540 fetched, 522,213 updated,
  **11,060 inserted**, 26,267 no-op. Row count grew by exactly 11,060.
  Watermark advanced `2026-08-20T02:23:09.123Z` → `2026-08-21T02:49:13.094Z`
  — verified via a live `$group` query that this equals the actual maximum
  `:updated_at` in the fetched batch, not an arbitrary value. Partition-level
  assertion held throughout (zero exceptions across a run touching many
  partitions with genuine inserts and updates) — its first real exercise on
  non-synthetic data.
- **Characterized the 522,213 "updated" rows directly, not assumed**: 99.997%
  (522,196) share one exact timestamp, `2026-08-21T01:33:31.100Z` — the same
  ~01:33 UTC daily signature seen on Aug 19 (526,605-row spike) and Aug 20
  (~13,537-row regular touch). **This is a recurring republish pattern, not a
  one-time anomaly** — three consecutive days now show a batch touch at the
  same time-of-day, with wildly varying magnitude (13.5K, 522K). The 11,060
  genuine inserts are cleanly distinguishable from this (spread across many
  distinct `:updated_at` values, consistent with organic new-row creation at
  roughly the expected ~7,900/day rate given the backfill's own cutoff).
- **Republish-noise ratio, fresh measurement**: 15.5x (vs. the original
  22.7x) — both measurements now on record, both dominated by the same
  recurring republish pattern. Two data points, same qualitative conclusion:
  incremental-run fetch volume is not "small" in the naive sense whenever a
  daily batch touch lands inside the query window, which given the observed
  ~daily cadence is often.
- **Run 7** (immediate follow-up): 545,988 fetched, 0 updated, 0 inserted,
  100% no-op, 0 row/snapshot delta. Proves idempotency *after* genuinely
  absorbing new content — the gap the original run 3/4 pair left open (those
  only proved idempotency when nothing had changed at all, per the first
  Gate 1 correction).

**All three load-bearing/flagged criteria now PASS on their own stated
conditions:**
- **Criterion 7**: idempotency proven (run 7) and insert path proven (run 6,
  non-zero insert count, row count grew by exactly that amount) — both
  halves of the original pass condition satisfied.
- **Criterion 8**: third-run-adds-≈-zero proven against fresh activity (run
  7 following run 6), not just against a static no-op window.
- **Criterion 11**: three independent real snapshot-diff demonstrations on
  record (#169→#170 delete; pre/post-run-6 insert; the 4,521-row update
  evidence), none contingent on a bug-fix narrative to be valid evidence.

No contingency needed: inserts did not come back zero, so the "determine
whether the publish cycle has run" fallback instruction doesn't apply here —
it clearly had, and the evidence shows it.

## Phase 2

### C2.1 — dbt→DuckDB→Iceberg read path: verified, direct reads recommended

- **Installed:** `dbt-core` 1.12.3, `dbt-duckdb` 1.11.0, pinned from actual
  resolution (matches the Phase 0 dry-run probe). `duckdb` stayed at 1.5.5 —
  dbt-duckdb's `duckdb>=1.0.0` constraint didn't force a change.
- **Tested in a throwaway project** (not the real `dbt/` scaffold — that's
  C2.2, gated behind H2.1 approval): a minimal `profiles.yml` with
  `extensions: [iceberg]` and `settings: {unsafe_enable_version_guessing:
  true}`, and one model doing `select count(*) from iceberg_scan('<bronze
  root>', allow_moved_paths => true)`.
- **Result: works cleanly.** `dbt run` created the view without error;
  `dbt show` (a genuinely fresh CLI invocation, fresh connection) returned
  **7,533,132** — an exact match to bronze's known row count. Confirmed the
  `iceberg` extension load and the `unsafe_enable_version_guessing` setting
  both persist correctly through dbt-duckdb's connection handling, across
  separate dbt invocations, not just within one long-lived process.
- **Performance measured, both scan types:**
  - Full-table `count(*)`: 0.088s. Partition-pruned `count(*)` (one month):
    0.061s. Both near-instant — DuckDB satisfies a bare count from Iceberg
    manifest-level statistics without touching Parquet data.
  - **Full-table materialization of every column** (the realistic cost a
    staging model actually pays): **54.2s** for all 7,533,132 rows.
  - **Fallback comparison** (PyIceberg/DuckDB export to a single Parquet
    file, then read that): export itself cost 9.5s; reading the exported
    Parquet back (all columns) cost 41.6s. **Not dramatically faster** — a
    ~23% reduction on the read side, and that's before even counting the
    export step's own 9.5s, which would need to repeat before every dbt run
    unless done on a separate, staler cadence.
- **Recommendation: keep direct Iceberg reads. Do not build the Parquet-export
  fallback.** The performance gap is real but modest (not the "dramatically
  slower" threshold phase-2.md asked about), and direct reads preserve the
  "dbt reads Iceberg directly" claim without adding a materialization step,
  an extra scheduled job, or a staleness window between bronze and what dbt
  sees. If the 54.2s full-materialization cost becomes a real dev-loop
  friction point once more models exist, the first lever to pull is
  partition-pruned staging (only re-read changed partitions), not switching
  away from direct reads entirely.
- **Not yet decided (deferred to C2.2/H2.1 approval):** the exact mechanism
  for defining bronze as a dbt **source** vs. hand-writing `iceberg_scan(...)`
  in every model. This throwaway test used a raw SQL model to isolate the
  read-path question cleanly; C2.2's actual scaffold needs to decide the
  cleanest way to expose this (a source with a custom scan macro, or a
  staging-layer convention) — a scaffolding decision, not a read-path one.
- **Stopping for H2.1 approval, per instruction** — no modeling begins until
  the human approves this read-path choice.
- **H2.1: APPROVED (direct reads, no fallback).** Three follow-up additions
  from the approval, addressed in C2.2 below.

### C2.2 — dbt project scaffold, and the three H2.1 follow-up additions

- **Scaffold:** `dbt/` with `models/{staging,intermediate,marts}`, `macros/`,
  `tests/`, `seeds/`, `analyses/`. Self-contained `profiles.yml` in the
  project directory (not `~/.dbt/`) for reproducibility — invoke as `cd dbt
  && dbt <command> --profiles-dir .`, not `--project-dir dbt` from the repo
  root (found empirically: dbt-duckdb resolves the profile's `path:` setting
  relative to the process's cwd, not `--project-dir`, so the two invocation
  styles aren't interchangeable — pick one convention and stick to it).

**Addition 1 — bronze as a dbt SOURCE with a scan macro.** Implemented with
one real constraint discovered along the way: dbt-duckdb's
`SourceConfig.external_location` (`relation.py`) renders its Jinja in a
context where **custom project macros are not yet in scope** — a
`{{ bronze_iceberg_scan() }}` call there failed with "macro is undefined"
(verified directly, not assumed). `{{ var(...) }}` calls **do** resolve
there, since vars are core Jinja context, not a project-macro lookup. Final
design: `dbt_project.yml` holds `bronze_warehouse_root` as the one place the
actual filesystem path lives; `models/staging/_sources.yml`'s
`meta.external_location` inlines `iceberg_scan('{{ var("bronze_warehouse_root")
}}', allow_moved_paths => true)`; `macros/bronze_scan.sql` defines the
equivalent expression as a real, callable macro for any future ad-hoc
analysis that needs the same scan outside a dbt-source context (where macro
calls work normally). Both read the same one var, so there remains exactly
one place to change the path — the "one macro" framing from the review
comment is satisfied in spirit (one source of truth) even though the literal
macro isn't invoked from inside the source YAML, for the structural reason
above. Verified end-to-end: `dbt run --select _smoke_test_source` (a
temporary throwaway model, deleted after verification, per C2.2's own scope —
staging is C2.4) returned **7,533,132** via `{{ source('bronze',
'service_requests') }}`, exact match to bronze's known count.
- **`dbt source freshness` verified working**, the concrete Phase 6 payoff
  this design was for: `loaded_at_field: updated_at`,
  `warn_after: {count: 3, period: day}`, `error_after: {count: 10, period:
  day}` — provisional thresholds informed by C1.1/C1.1b's measured lag
  (2.53-day floor, 6.71-day median, up to 92-day tail): tighter than the
  median so real slowdown surfaces early, looser than the median to avoid
  paging on ordinary variance, well inside the 92-day tail either way.
  Ran `dbt source freshness` live: **PASS** (max `updated_at` is recent,
  from run 6). Revisit these thresholds with real Phase 6 operational data
  before trusting them in production alerting.

**Addition 2 — dev-time row limit, target-aware project var.**
`dev_row_limit` in `dbt_project.yml`'s `vars:`, set via
`"{{ 90 if target.name == 'dev' else none }}"`. Verified empirically with a
temporary debug macro (removed after verification) that `target.name` **is**
available when `dbt_project.yml`'s vars are rendered (not guaranteed in all
dbt versions/contexts, so this was checked rather than assumed): `dbt
run-operation ... --target dev` resolved `dev_row_limit=90`; `--target prod`
resolved `dev_row_limit=None`. The var is defined now (C2.2); it isn't
*applied* anywhere yet — that's C2.4's staging model, which will filter to
the most recent `dev_row_limit` days of `created_date` when the var is set,
and read unfiltered when it's null. Switching targets is purely a `--target`
flag change, never an edited model, per the instruction.

**Addition 3 — Phase 7 benchmark baselines captured now.** Recorded in
`docs/metrics.md` with the environment (Apple M3 Pro, 18 GB RAM, arm64,
macOS 15.7.3, DuckDB 1.5.5, dbt-duckdb 1.11.0) alongside, and with the
count-only queries explicitly flagged as **zero-bytes-scanned metadata reads**
— Athena has no equivalent free path for the same query, so Phase 7's
comparison must report bytes-scanned and dollar cost alongside latency, not
latency alone, or the comparison is unfair by construction.

**Cleanup:** the smoke-test model and debug macro were both temporary,
created to verify specific claims empirically and deleted immediately after.
`.user.yml` (dbt's local anonymous-usage-tracking ID, created because
`--profiles-dir .` pointed dbt's user-config lookup at the project directory
too) added to `.gitignore` — not project source.

### C2.3 — Proposed observation cutoff: 30 days

**Proposal: `observation_cutoff_days = 30`.** A row is `is_settled` only if
`created_date <= (max available created_date - 30 days)`, independent of its
current open/closed status. This is a temporal trust boundary, not a
closure-status one — a row can be `is_settled=true` and still open (a
genuine, trustworthy censoring case) or `is_settled=false` regardless of what
bronze currently shows for it (too recent to trust either reading).

**Reasoning, against the actual measured distribution** (C1.1: 2.53-day
floor, 6.71-day median, up to 92-day tail):
- **30 days is ~4.5x the median lag (6.71 days)** — comfortably clears the
  typical case with real margin, not just barely past it. Most genuine
  closures that are going to be reflected promptly will be reflected well
  before this boundary.
- **30 days is roughly 1/3 of the 92-day tail** — this is a deliberate,
  stated trade, not an oversight. **No finite cutoff shielded by the 92-day
  tail is achievable without discarding nearly three months of the most
  analytically interesting (most recent) data.** A cutoff at 30 days accepts
  a residual: some small fraction of requests older than 30 days will still
  have a late-arriving closure update beyond that point, and their
  `is_settled=true` classification will turn out to have been premature. This
  is a real, quantifiable cost — not eliminated, just bounded and disclosed —
  and C2.8's real measurements (closure rates, censored counts) will surface
  its actual size rather than leave it theoretical.
- **30 days is one calendar month** — a legible, easily communicated unit for
  a dashboard's "last N days excluded from resolution-time metrics" framing,
  which matters since this cutoff is an analytical judgment call the human
  audience (not just downstream code) needs to understand and trust.
- **Distinct from, and compounding on top of, the already-handled censoring
  problem.** `is_censored` (genuinely still-open) and `is_settled` (recent
  enough that even a "closed" reading might not be final) are two separate
  axes — a resolution-time metric should require *both* `is_closed=true` and
  `is_settled=true` to be trusted; the mart computes `resolution_hours` as
  null whenever either condition fails, never a zero or imputed value (per
  C2.5's explicit requirement).

**Not proposed:** a cutoff at the 6.71-day median (too much of the
distribution's mass still resolves after that point — a materially high
false-settled rate) or at the 92-day tail (technically safest, but discards
enough recent data to defeat the project's own stated purpose — a dashboard
that draws the eye to exactly the period it would have to exclude).

**Recorded as the project variable** `observation_cutoff_days` in
`dbt/dbt_project.yml` (currently `null`, placeholder pending approval — will
be set to `30` once H2.2 clears, not hardcoded in any model).

**Stopping for H2.2 approval, per instruction.** `int_request_resolution`
will not be built until this is approved.
- **H2.2: APPROVED at 30 days.** One follow-up requirement: measure the
  actual settlement curve directly (not inferred from C1.1's lag
  distribution — a different quantity, see below) before building
  `int_request_resolution`.

### C2.3 follow-up — measured settlement curve, cutoff revised to 45 days

**Why this is a different measurement from C1.1.** C1.1 measured
`closed_date`-to-`:updated_at` lag (2.53d floor, 6.71d median, 92d tail) —
the delay between a closure happening and it becoming visible. The
observation cutoff needs *created_date*-to-observable time — how long after
a request is filed until its eventual closure (whenever that closure
happens) is visible — which also folds in however long the underlying
resolution itself took. These are genuinely different quantities; inferring
one from the other would have been a real gap, correctly caught.

**Method:** for each of several fully-settled creation cohorts,
`date_diff('day', created_date, updated_at)` on every currently-closed row
gives, per row, days-until-observable directly from data already in bronze
— no need to reconstruct historical API snapshots.

**A real methodological trap was hit and diagnosed, not glossed over.**
First attempt used 2024/2025 cohorts (per the review comment's own
suggestion) and returned **0.00% completeness at every threshold up to 90
days** — an implausible result investigated immediately rather than
reported at face value. Root cause: the December 2025 migration (Phase 1's
finding) bulk-touched `:updated_at` for effectively the whole pre-existing
dataset, so for any row whose genuine closure predated that migration,
`updated_at` reflects the *migration's* timestamp, not the original
observability moment — 481 days after creation for a September 2024 sample,
confirmed directly. **Cohorts were redrawn from 2026 (Jan-Apr)** — old
enough by Aug 2026 to clear the 92-day tail, but created after the Dec 2025
migration, so this specific contamination doesn't apply to them.

**A second, smaller contamination found and excluded, not blended in.**
January 2026 showed 26.9% of its closed rows (91,010 of 338,952) sharing one
exact `updated_at` — `2026-08-20` — distinct from the broader Aug 19-21
recurring pattern documented earlier this phase. Checked whether Feb/Mar/Apr
show the same issue: they show only 0.10-0.24% touched by any recent-spike
window — negligible. January is reported in `docs/findings.md` for
completeness but **excluded from the cutoff conclusion**, since it's
identifiably contamination, not signal. Flagged as its own candidate finding
for Phase 3 (a plausible one-off administrative backlog sweep on ~7-month-old
cases) — related to, but distinct from, C2.8's bulk-closure hypothesis.

**Result (clean cohorts, Feb/Mar/Apr 2026, full table in
`docs/findings.md`):**

| N | 2026-02 | 2026-03 | 2026-04 | Average |
|---|---|---|---|---|
| 30d | 85.45% | 85.23% | 87.25% | **86.0%** |
| 45d | 92.06% | 92.98% | 93.92% | **92.99%** |
| 90d | 97.29% | 98.27% | 98.97% | 98.18% |

**At 30 days: ~86% — materially below the ~90% threshold specified for
"well justified."** Per the pre-agreed decision rule, this is a revision
case, not a confirmation case.

**Revised: `observation_cutoff_days = 45`.** Clears ~93% consistently
across all three clean cohorts (92.06-93.92%, a tight range — not a noisy
or marginal pass). Updated in `dbt/dbt_project.yml`, replacing the
placeholder `30` that was never actually committed to the project var (H2.2
approved the *concept* at 30; this measurement was required before building
anything on it, and changed the number before it was ever used). Still an
explicitly disclosed trade against the 92-day tail (98% at 90 days, not
100%) — no finite cutoff is risk-free, this one is now evidence-backed
rather than inferred.

**`is_settled` confirmed to key on `created_date`, not `closed_date`, per
the explicit check requested.** Keying on `closed_date` would exclude
exactly the slow-resolving/still-open requests the cutoff exists to
account for: an open row has no `closed_date` at all, so it could never be
marked settled regardless of true age (permanently "too recent" no matter
how long it's been open); a row that *just* closed would get
`closed_date ≈ now` and pass a recency-based settledness check immediately —
inverting the correction, treating the least trustworthy reading (a
just-happened closure, likely still subject to revision/lag) as the most
trustworthy. `created_date`-keying correctly makes "old enough since
creation" the trust signal, independent of current status, which is what
lets a long-open row be a confident, genuine censoring case rather than an
indefinitely-untrusted one.

### C2.4 — Staging: three ambiguities resolved by direct measurement

- **`latitude`/`longitude` vs `location_lat`/`location_lon`**: 100.0000%
  agreement (7,403,755 of 7,403,755 rows where both pairs are present, within
  0.0001° / ~11m) and always both-present-or-both-null together (0 rows with
  only one pair populated). `latitude`/`longitude` kept as canonical
  (simpler names, no GeoJSON-derivation step); the `location_*` pair dropped
  entirely past staging as fully redundant.
- **Coordinate validity**: a `has_valid_coordinates` boolean flag, never a
  row filter, per phase-2.md — out-of-bounds is 0% (confirmed again here)
  but missing reaches ~1.7% in this table snapshot, so presence/absence is
  the only real distinction worth flagging.
- **`resolution_action_updated_date`**: kept in staging, not bronze-only —
  real analytical value (a source-side "last resolution update" signal used
  as the C1.8 cross-check field in Phase 1) and no reason to hide it.
- **Casing normalization, checked field-by-field rather than applied
  blanket-uniformly**: `borough`, `status`, `agency`, `open_data_channel_type`,
  `park_borough` all showed zero case-insensitive variants (verified, not
  assumed) and were left with only `trim()`. `city`, `complaint_type`, and
  `descriptor` showed real variants (`'NY'/'ny'/'Ny'`, `'Plumbing'/'PLUMBING'`,
  `'Elevator'/'ELEVATOR'`) and were normalized to uppercase, matching the
  schema's otherwise-dominant convention. This matters concretely for
  `dim_complaint_type`'s grain — without normalizing, case variants would
  have counted as distinct dimension members.
- **Timezone assumption operationalized, not just documented**: naive
  timestamp columns are localized via `timezone(var('created_date_timezone'),
  ...)` in staging — the one place phase-2.md's problem #3 asks for — proving
  along the way that DuckDB's `timezone()` is DST-aware (EST in January, EDT
  in July, verified directly), resolving Phase 1's "DST-awareness unverified"
  open question.

### C2.5 — Intermediate: `int_request_resolution` and `int_request_geography`

- **`int_request_resolution`** implements exactly the three-flag design
  phase-2.md specifies: `is_closed` (status='Closed' AND closed_date not
  null — status alone isn't sufficient given the known "Closed, null
  closed_date" defect), `is_settled` (created_date old enough per the
  measured 45-day cutoff — keyed on created_date, confirmed correct above),
  `is_censored` (NOT is_closed OR NOT is_settled). Verified directly: querying
  all four (is_closed, is_settled) combinations shows `resolution_hours`
  populated in *exactly* the is_closed=true/is_settled=true cell and null in
  all three others — the C2.9 test target, already true before the test is
  even written. The rare closed-before-created defect (~0.02-0.03%) is left
  un-suppressed in `resolution_hours` (produces a negative value) rather than
  nulled out separately — hiding it would mask exactly the finding C2.9's
  `closed_date >= created_date` test is designed to surface.
- **`int_request_geography` — borough vs park_borough resolved by direct
  measurement**: 100.00% agreement across 7,526,843 non-Unspecified rows,
  and identical null/Unspecified pattern in exactly the same 6,289 rows
  (0.08%) for both — not a park-complaint-specific field with sparser
  coverage as the name suggests, just a duplicate. `borough` kept, `
  park_borough` dropped.
- **`community_board` used directly as the location key**, not re-derived:
  format "NN BOROUGH" (e.g. "12 BRONX"), 77 distinct raw values, 0 nulls,
  with graceful "Unspecified BOROUGH" / "0 Unspecified" fallbacks — this
  already *is* phase-2.md's "borough + community district," combined.
  Splitting and rejoining it would only reconstruct the same string with
  more chances to disagree with the source's own encoding.

### C2.6/C2.7 — Dimensions, and a real grain-violation bug caught and fixed

- **Dimensions**, Type 1, `row_number()`-keyed surrogates (no external
  package dependency added for this): `dim_agency` (16 members, agency ->
  agency_name verified 1:1, no drift), `dim_complaint_type` (202 distinct
  `complaint_type` values, 1,278 distinct (complaint_type, descriptor) pairs
  — a real ~6.3-descriptor-per-type hierarchy, descriptor null 0.33% of the
  time and preserved as a genuine null, never coalesced into a placeholder
  string in the displayed attribute), `dim_location` (keyed on the full
  (community_board, borough) pair — see below for why both, not
  community_board alone), `dim_date` (one row per day, 2024-08-19 through
  2027-12-31, 1,230 rows, includes season derived from month for the
  compositional-seasonality question C2.8 asks).
- **A real grain violation was caught and fixed before it reached tests,
  not discovered by them.** `fct_service_requests`'s first build showed
  1,024,548 total rows for only 982,790 distinct `unique_key`s — a fan-out.
  Root cause: `dim_location`'s declared grain is the pair
  `(community_board, borough)`, but the fact table's join initially matched
  on `community_board` alone. Investigated directly: **646 rows have a
  genuinely inconsistent `community_board`/`borough` pairing in the source**
  — 645 rows with `community_board='08 BRONX'` but `borough='MANHATTAN'`,
  1 row with `community_board='01 QUEENS'` but `borough='BRONX'` — meaning
  `dim_location` legitimately has 2 borough values for each of those two
  community_board strings, and joining on community_board alone matched
  both. **Fix: join on the full (community_board, borough) pair**, matching
  what `dim_location`'s grain actually is — not a workaround, the original
  join was simply incomplete relative to the dimension's own declared key.
  Verified after the fix: 982,790 = 982,790, zero null FKs across all four
  dimensions. **The underlying 646-row inconsistency itself is a real data
  quality finding**, worth carrying into Phase 3's DQ scorecard — a small
  but genuine source-data defect distinct from anything found in Phase 0/1.

### C2.8 — Five analytical questions, answered against the full prod build

Full findings and tables in `docs/findings.md`. Two are worth flagging here
because they surfaced new data-quality/interpretation nuance beyond what
the question asked for:

- **Agency SLA**: EDC (49.6% closure) and TLC (70.5% closure) stand out
  not as slow agencies but as agencies where a large share of settled
  requests never reach `status = 'Closed'` in this dataset at all. Flagged
  as a Phase 3 DQ question (do these agencies close out-of-band, e.g. in a
  system that doesn't sync `status` back?) rather than reported as a
  literal "half of EDC's requests are abandoned" finding.
- **Geographic equity**: found two distinct shapes, not one. HEAT/HOT WATER
  shows a median-level gap (Bronx/Staten Island ~36-37h vs.
  Brooklyn/Manhattan/Queens ~44-46h). NOISE - RESIDENTIAL shows a
  same-median-worse-tail pattern instead — Bronx's p90 (12h) is 2-3x every
  other borough's (4-6h) while medians are all ~1-2h. A median-only SLA
  dashboard would completely hide the second pattern; both are reported.
- **Bulk-closure hypothesis**: fully confirmed and total, not partial —
  all 11,372 of the 2024 `Closed`+null-`closed_date` rows share one
  `:updated_at` date (2025-12-26), 99.9% belong to DHS, and 94% carry one
  of two DHS mobile-outreach template resolution texts. A single
  administrative bulk-closure sweep, not scattered data entry gaps.
- **Geocoding-lag hypothesis**: the naive framing (monotonic rise = lag)
  was falsified by the data — the spike is at March 2026, five months
  before the most recent data, which a genuine "hasn't caught up yet" lag
  cannot produce. Traced instead to a real compositional effect: NYC's
  known spring pothole surge inflates STREET CONDITION's volume ~5-6x in
  March-April, and STREET CONDITION chronically has a far higher
  missing-coordinate rate (21-46%) than the dataset overall (1.72%) because
  it's commonly reported by street segment rather than a point location.
  The dataset-wide rate rises and falls with STREET CONDITION's share of
  monthly volume, not with any change in per-type geocoding behavior. This
  is the kind of wrong-initial-hypothesis-caught-by-measurement finding
  CLAUDE.md's standing rules exist to surface rather than paper over.

### C2.9 — Tests and model contracts

- **A real bug caught by writing contracts, not by inspection**:
  `dim_date.date_key` was typed `TIMESTAMP`, not `DATE` — DuckDB promotes
  `date + interval` to `TIMESTAMP` — while `fct_service_requests.date_key`
  is `DATE` (`cast(created_date as date)`). The two would still join
  correctly today via implicit cast, but a declared model contract can't
  paper over a type mismatch, and leaving it would have made the
  `relationships` test's declared `field: date_key` a lie about the actual
  types on both sides. Fixed by casting the date spine back to `DATE`
  explicitly in `dim_date.sql`, with a comment explaining why the cast is
  necessary rather than redundant. This is exactly the kind of thing model
  contracts are supposed to surface early — caught here, in Phase 2, not
  discovered by a downstream BI tool silently coercing types in Phase 5.
- **Model contracts enforced on all four dimensions and the fact table**
  (not staging/intermediate, per phase-2.md), with every column typed
  explicitly. No `dbt_utils` or other package dependency added — every test
  here is either a core dbt generic test (`unique`, `not_null`,
  `relationships`) or a hand-written singular SQL test in `tests/`, since
  the full minimum list in phase-2.md doesn't require anything a package
  would add, and adding one mid-phase for two tests wasn't worth the extra
  dependency surface.
- **`assert_resolution_hours_null_when_censored`** — hard error, zero
  tolerance. Unlike the closed/created defect below, there is no known
  legitimate reason for this to ever fail; it's the direct proof of STOP
  GATE 2's load-bearing criterion 5.
- **`assert_closed_date_after_created_date`** — deliberately configured to
  **warn**, not error, and to warn on `>0`: this test is *designed* to show
  a small number of failures every run (1,763 rows / 7,289,995 with a
  `closed_date`, ≈0.0242% in the full prod build — matching the ~0.02%
  rate already documented from Phase 1). `error_if: '>3500'` is the real
  assertion: it only escalates to a hard failure if the defect rate roughly
  doubles, which would mean something new and undiagnosed broke, distinct
  from the known Phase-1-documented defect. Verified this design works as
  intended in both targets: prod run shows `WARN 1763`, dev run (recent
  ~90-day slice) shows `WARN 354` — same underlying rate, proportionally
  smaller count, neither anywhere near the error threshold.
- **`assert_staging_reconciles_to_bronze`** — target-aware by necessity,
  not convenience: dev's `dev_row_limit` sampling means staging *should*
  differ from bronze's full count there, so the test no-ops in dev
  (`select 1 where false`) and does the real comparison only in prod, where
  it confirms exact equality (7,533,132 = 7,533,132).
- **Result**: `dbt build --target prod` → 5 table models, 3 view models, 42
  data tests, **49 PASS / 1 WARN (as designed) / 0 ERROR**, full run
  (models + tests) in **~10-12s**. Same shape in dev (49 PASS / 1 WARN / 0
  ERROR), confirming the target-aware tests behave correctly in both
  environments rather than just happening to pass in one.
- **Complaint-type dimension count correction**: while re-measuring for
  this section, `dim_complaint_type` came back as 202 distinct
  `complaint_type` values / 1,278 distinct pairs, not the 206/1,280 recorded
  in the C2.6/C2.7 entry above — corrected there and in the model's own
  comment/schema description. Bronze's total row count is unchanged
  (7,533,132), so this reflects underlying field values shifting via
  upstream MERGE/upsert activity between measurements, not a row-count
  change — a reminder that dimension cardinality in an actively-updated
  source isn't a one-time-measured constant, and any hardcoded row-count
  comment here is a snapshot, not a guarantee.

## Phase 3

### C3.1 — Soda/DuckDB dependency conflict: resolved, evaluated empirically in the stated order

Phase 0 logged `soda-core-duckdb` pinning `duckdb<1.1.0` against the
project's DuckDB 1.5.5, deferred to here. Tested each phase-3.md option in
order, on real artifacts, not assumed:

- **Option 2 (check whether the pin still holds) — ruled out.** Downloaded
  every currently-published `soda-core-duckdb` release; the latest
  (3.5.6) still ships `Requires-Dist: duckdb<1.1.0` in its wheel metadata.
  The pin hasn't moved.
- **Option 1 (separate venv, invoked as subprocess) — proven to work end
  to end.** Built an isolated Python 3.11 venv with `soda-core-duckdb`
  (pulling DuckDB 1.0.0 transitively) and ran a real `soda scan` against
  `dbt/target/openledger_prod.duckdb` — the actual file dbt-duckdb 1.11.0 /
  DuckDB 1.5.5 built. Two checks (`row_count > 0`,
  `missing_count(agency_key) = 0`) against `fct_service_requests` both
  **PASSED**. Two things verified along the way, not assumed:
  - **DuckDB's on-disk storage format is forward-readable across this
    range** — DuckDB 1.0.0 opened a database file written by 1.5.5 without
    error or migration, for every materialized table checked. Storage
    format versions increment far less often than library versions; this
    range happens to share one.
  - **The old venv's bundled `iceberg` extension cannot read the
    staging/intermediate views** — they call `iceberg_scan()` with
    `unsafe_enable_version_guessing`, a setting the older iceberg extension
    doesn't recognize (`CatalogException: unrecognized configuration
    parameter`). This does **not** block Soda for its actual job: C3.4's
    distributional checks all target the **materialized marts layer**
    (`fct_service_requests` and the four dimensions), which are plain
    DuckDB tables at that point, not live Iceberg reads. Scoping Soda's
    checks to marts-only isn't a workaround — it's already exactly where
    the phase's distributional checks belong.
- **Option 3 (Soda against exported Parquet) — not pursued.** Would work,
  but reintroduces the same export-plus-staleness-window tradeoff already
  rejected for the read path itself in C2.1/H2.1 ("23% on a once-per-build
  step doesn't justify an export job plus a staleness window"). Option 1
  already works with no export step at all, so there's no reason to pay
  that cost a second time for the same kind of gain.
- **Option 4 (drop Soda for `dbt_expectations`) — not pursued.** A
  legitimate fallback if Option 1 had failed, but CLAUDE.md's locked
  decisions table specifically calls out Soda Core as the deliberate
  choice over Great Expectations for this timeline, and names it as a
  resume line. Since Option 1 has no discovered blocker, there's no
  forcing reason to give that up.

**Recommendation: Option 1** — a separate venv for Soda (not yet made
permanent in the repo; that's C3.4's job once approved), invoked as a
subprocess after `dbt build` completes (sequential, not concurrent, so no
file-lock contention with dbt's own connection), scoped to the marts
layer only. Proposing this for **H3.1** before building out C3.4's real
checks or committing the venv/requirements structure to the repo.

**H3.1 — APPROVED** (Option 1). Three requirements attached, to be
satisfied when C3.4 makes this permanent: (1) pin both environments
explicitly via separate `pip freeze`-derived requirements files, both
recorded in `docs/versions.md` with DuckDB version called out in each,
and explicit that the 1.0.0-reads-1.5.5 compatibility is verified for
today's versions only, not guaranteed going forward; (2) the Soda runner
must assert read-compatibility at scan start and fail with an explicit,
named error rather than surfacing a raw storage exception if the
environments have drifted; (3) empirically test (not design around) what
happens when a Soda scan runs while a dbt build holds the same database
file open. All three addressed in the C3.4 entry below.

### C3.2 — Model contracts extended to staging and intermediate

Phase 2 enforced contracts on the four dims + fact table only. Extended
to `stg_service_requests` and both intermediate models, so contracts now
cover every model in the project (nothing in `models/staging` or
`models/intermediate` is contract-free anymore):

| Model | Layer | Columns contracted |
|---|---|---:|
| `stg_service_requests` | staging | 49 |
| `int_request_resolution` | intermediate | 7 |
| `int_request_geography` | intermediate | 7 |
| `dim_agency` | mart | 3 |
| `dim_complaint_type` | mart | 3 |
| `dim_location` | mart | 3 |
| `dim_date` | mart | 11 |
| `fct_service_requests` | mart | 18 |
| **Total** | | **8 models, 101 columns** |

Confirmed contracts hold in both `dev` and `prod` targets — `dbt build`
completes clean in each (dev: 50 PASS / 1 by-design WARN / 0 ERROR; prod:
same shape), including on the two models materialized as **views**
(`stg_service_requests`, both intermediates) — contract enforcement is
not table-only in dbt-duckdb, confirmed empirically rather than assumed.

**Deliberate failure demonstrated, per phase-3.md's requirement that an
untested contract is untested**: changed `int_request_resolution`'s
declared `resolution_hours` type from `bigint` to `varchar` and ran
`dbt run --select int_request_resolution`. Failed immediately, at compile
time, before any data was touched:

```
Compilation Error in model int_request_resolution
This model has an enforced contract that failed.
| column_name      | definition_type | contract_type | mismatch_reason    |
| resolution_hours | BIGINT          | VARCHAR       | data type mismatch |
```

Reverted the type back to `bigint`; re-ran the same model; passed clean.
This is the actual value of a contract over a data test: the failure
fires on the **model definition**, before a single row is materialized or
queried — a data test can only ever catch a problem after the fact.

### The `is_settled` session-timezone bug — the strongest catch in Phase 3 so far (README-bound)

Found while designing C3.3's 45-day boundary unit test, and worth its own
entry rather than being buried as a preface to that section — this is the
kind of finding that belongs in the README's limitations/findings section,
not just the journal.

**Mechanism.** `int_request_resolution.sql`'s `is_settled` expression
compared `created_date` (localized to `created_date_timezone` —
America/New_York — in staging) against bare `current_date`. `current_date`
in DuckDB resolves against the **session's** `TimeZone` setting — a
connection-level configuration value — not against this project's
`created_date_timezone` var. Those are two different things that happen to
produce the same answer only when the session's TimeZone setting is itself
set to America/New_York. Nothing in `dbt_project.yml` or `profiles.yml`
pins the session TimeZone anywhere; it was silently inherited from
whatever DuckDB defaults to on the machine running the build.

**Why it passed locally, every time, for the entire project so far.** This
dev machine's DuckDB session defaults its TimeZone setting to
`America/New_York` — confirmed directly (`current_setting('TimeZone')` →
`America/New_York`) — purely because that's this machine's own local
timezone, not because of any project configuration. Every `dbt build`, every
manual query, every test run up to this point ran in a session where the
two quantities (session TimeZone vs. `created_date_timezone`) coincided by
environmental accident. There was no way to observe this bug from local
development, no matter how much testing ran here — the discrepancy is
invisible until the session's TimeZone setting differs from
America/New_York.

**What it would have corrupted.** Precisely measured the affected window,
correcting an earlier imprecise estimate along the way (this project's own
"verify, don't assume" standard applied to itself): for a session running
in UTC — the default for Phase 6's GitHub Actions runners — the session's
calendar date rolls over to the next day 4 hours before New York's actual
midnight during EDT (20:00–23:59:59 Eastern), or 5 hours before during EST
(19:00–23:59:59 Eastern), verified directly against concrete instants in
both offsets. Any `dbt build` that happened to run inside that nightly
window would compute the 45-day settlement cutoff as one full calendar day
later than the correct America/New_York-anchored cutoff — pushing every
row created exactly 45 (or 44, right at the edge) days ago back into
`is_censored = true` for that run, incorrectly. Since `resolution_hours`
is null for every censored row, this would have **silently dropped the
oldest, most-recently-settled cohort out of every SLA metric computed from
that build** — median/p90 resolution hours, closure rates, the settlement-
completeness tracker itself (C3.5d, below) — for one calendar day, on
whichever nights the CI schedule happened to hit that window, with no
error, no test failure, and no visible symptom beyond numbers that were
quietly one day more pessimistic (or optimistic, depending on which rows
crossed the boundary) than they should have been. Exactly the "off-by-one
in `is_settled` ... produces no error anywhere" failure mode phase-3.md
names as this phase's load-bearing risk (criterion 3) — found here before
Phase 6 ever ran a build on infrastructure where it would have triggered.

**The fix.** `is_settled` now anchors explicitly to
`timezone(var('created_date_timezone'), current_timestamp)::date`, which
extracts the calendar date in a *named* zone regardless of session state,
instead of session-dependent `current_date`. Verified the fix is
session-invariant (`SET TimeZone='UTC'` no longer changes the anchor date)
and re-ran the full `dbt build` — unchanged results, all tests still 50
PASS / 1 by-design WARN / 0 ERROR, confirming this was a latent,
not-yet-triggered bug on this machine specifically, not a behavior change
to any number already reported in `docs/findings.md`.

### C3.3 — dbt unit tests

**7 native `unit_tests:` nodes on `int_request_resolution`**, mocking
`ref('stg_service_requests')` (refs introspect cleanly — no issue here):
normal close (48h), the closed-before-created defect passed through as a
genuine negative value (not clipped/suppressed — matches the design
documented in C2.5), an open request always censored, a request closed
but not yet settled yielding null (not a computed value), and the three
45-day boundary cases.

**A real methodology bug caught while writing the first boundary test,
worth recording as evidence the tests are actually exercising the logic
and not just rubber-stamping it**: the first attempt at "exactly 45 days"
used noon of the target day and **failed** — `is_settled` came back
`false`, not `true`. Root cause: the model's boundary compares against
**midnight** of (today − 45 days) in `created_date_timezone`, not "any
time during the 45th day" — a row created at noon on the boundary day is
*after* that midnight instant, so it correctly reads as not-yet-settled
under the actual `<=` comparison. Fixed by anchoring all three boundary
fixtures (44/45/46 days) to midnight instead of noon, matching the
model's real comparison point exactly. Re-ran: all 7 pass. Recorded here
rather than quietly correcting it, since a test that fails once for the
right reason and is then fixed for the right reason is worth more than a
test that happened to pass on the first try.

**Timezone-at-a-day-boundary case — implemented as a singular data test
instead of a native unit test, for a documented reason**: a unit test on
`stg_service_requests` would need to mock `source('bronze',
'service_requests')`, but that source is a raw `iceberg_scan(...)`
expression inlined via `meta.external_location` (see
`models/staging/_sources.yml`) — it never becomes an actual catalogued
DuckDB relation. dbt-duckdb's unit-test fixture builder needs to
introspect a real relation's columns for a `source()` input and errored
(`Not able to get columns for unit test ... because the relation doesn't
exist`) every time, confirmed structural (not transient) by retrying with
every source column explicitly specified in the fixture — same error
regardless. This is the same family of constraint Phase 2 found when a
macro couldn't be called from `meta.external_location` — a real,
recurring cost of the "source as inline SQL expression" pattern, not a
one-off. Worked around with
`tests/assert_timezone_localization_dst_boundaries.sql`, a singular test
asserting the identical `timezone(created_date_timezone, ...)` expression
against literal inputs (no source/ref dependency, so no introspection
needed), covering three cases verified once by hand before being pinned
down permanently: a normal instant; `2026-11-01 01:30:00`, an **ambiguous**
local time (DST fall-back — 1:30am occurs twice) which DuckDB resolves to
the second (EST, -05:00) occurrence, not the first; and `2026-03-08
02:30:00`, a **nonexistent** local time (DST spring-forward — clocks skip
2am to 3am) which DuckDB shifts forward by the gap (to 3:30am EDT) rather
than erroring. All three pass and now run on every `dbt build`.

**Honestly, this workaround is a weaker guarantee than a real unit test —
stated plainly, and for a specific reason, not the generic one.** It is
*not* weaker because it validates against real data instead of controlled
inputs — it doesn't; `assert_timezone_localization_dst_boundaries.sql`
uses literal constant timestamps (`timestamp '2026-11-01 01:30:00'`, etc.)
inside the test file itself, so it does exercise the exact ambiguous/
nonexistent instants deliberately, on demand, regardless of what data
happens to exist in any given build window — the same controlled-input
property a unit test provides. The real gap is different and more
specific: **this test never calls the model at all.** It re-implements the
`timezone(created_date_timezone, ...)` expression as a standalone
assertion against DuckDB directly, decoupled from
`stg_service_requests.sql`'s actual compiled SQL. A genuine unit test
(`unit_tests:` running the real model against mocked input, as the 7
`int_request_resolution` tests do) would catch a regression *in the
model* — e.g. someone refactors the localization call, applies it to the
wrong column, or drops it from one of the four date columns that need it.
This singular test cannot catch any of that, because nothing about it
depends on `stg_service_requests.sql`'s content — it would still pass
even if the real model's localization logic were deleted entirely. It only
proves DuckDB's `timezone()` function itself behaves a documented,
deterministic way at these three instants; it says nothing about whether
`stg_service_requests` is actually calling it correctly, on the right
columns, going forward.

**Does the constraint generalize?** Checked directly (`grep -rl "source("
models/`): **`stg_service_requests.sql` is the only model in the entire
project that references `source()` at all** — every other model
(`int_request_resolution`, `int_request_geography`, all four dimensions,
`fct_service_requests`) consumes only `ref()`s to other dbt models, none
of which hit this constraint. So the blocked set is exactly, and only,
**stg_service_requests's own transformation logic** — every unit test
that would want to mock the bronze source directly is blocked by the same
issue, not just the timezone one. Concretely, this also blocks native unit
tests for: `has_valid_coordinates`'s bounding-box/`(0,0)`-exclusion edge
cases, the casing-normalization logic (`complaint_type`, `descriptor`,
`city`, etc.), the descriptor-null-preservation behavior (never coalesced
into a placeholder), and the `dev_row_limit` target-aware filter. None of
these got a unit test in C3.3 for the same reason the DST case didn't —
they'd all need the same workaround (a singular test re-implementing the
logic outside the model) if tested at all, with the same "doesn't actually
call the model" weakness.

**This is a real, bounded structural limit, worth carrying into Phase 4**:
the entire staging layer's own transformation logic (as opposed to
staging's *output*, which downstream models can and do get real unit-tested
against) is permanently untestable via native dbt unit tests, for as long
as the bronze source stays declared as an inline `meta.external_location`
expression rather than a real catalogued relation. It doesn't block Phase
4 directly — MetricFlow's metrics are defined over `fct_service_requests`/
`int_request_resolution`, both fully `ref()`-based and fully unit-testable
— but it's the kind of limit that should be named once, here, rather than
rediscovered by surprise the next time someone reaches for a unit test on
staging logic.

**Result**: `dbt build --target prod` → 5 table models, 3 view models, 44
data tests, **7 unit tests** — 58 PASS / 1 by-design WARN / 0 ERROR. Same
shape in dev.

**A second, previously unknown timezone-adjacent trap, found 2026-08-23
while doing C3.6-C3.9 work**: `test_is_settled_one_day_short_of_boundary`
(the 44-day boundary case) failed on a `dbt build` run the day after this
suite was first built, with no code change — `actual=True` vs.
`expected=False`. Root cause was not the model: the unit test fixture's
`created_date` is rendered via Jinja (`modules.datetime.datetime.now(...)`)
**at dbt's parse step**, and `target/partial_parse.msgpack` had cached that
parse from the previous day's build. Because the YAML file itself hadn't
changed, dbt's partial-parse optimization reused the cached parse —
including the previous day's already-rendered "now" — instead of
re-evaluating the Jinja fresh, so the fixture's "44 days ago" silently
stayed anchored to yesterday's date while the model's own SQL
(`current_timestamp`, evaluated at query run time, not parse time) moved
forward normally. The two drifted apart by exactly one day, which was
enough to flip a boundary test. Confirmed by deleting
`target/partial_parse.msgpack` and rebuilding: full green, no code change.
**Any dbt unit test fixture built from a Python-Jinja "now" is at risk of
this on any day the project isn't fully reparsed** — a real Phase 6
concern (a CI runner that reuses a `target/` directory across scheduled
runs, exactly the kind of persistent-runner setup a naive cron job might
use, would need either a forced `--no-partial-parse`/clean `target/` on
each run, or these date-relative fixtures avoided in favor of literal
timestamps). Not a defect in `is_settled` itself — the model was correct
throughout; only the test fixture's cached input was stale.

### C3.4 — Distributional checks, and making Soda permanent (H3.1's three requirements)

**H3.1 requirement 1 — pin both environments explicitly.** Created
`.venv-soda/` (Python 3.11.15, separate from the main `.venv/`'s Python
3.14.7) and `requirements-soda.txt` via a real `pip freeze`, mirroring
`requirements.txt`'s existing convention. Both files now call out their
DuckDB version explicitly in a header comment (`duckdb==1.5.5` in
`requirements.txt`, `duckdb==1.0.0` in `requirements-soda.txt`), with an
explicit statement that the cross-version read compatibility is verified
for these exact pins only, not guaranteed going forward. Recorded in
`docs/versions.md`'s new "Phase 3 additions" section as a side-by-side
table. `.venv-soda/` added to `.gitignore` alongside `.venv/`.

**H3.1 requirement 2 — assert compatibility at scan start.**
`quality/run_soda.py` opens the real prod build with the Soda venv's own
`duckdb` library and runs an actual query against `fct_service_requests`
before invoking any checks. Tested that this actually fires, not just that
it's present: replaced `dbt/target/openledger_prod.duckdb` with a garbage
file and ran the script — it failed immediately with the intended named
message ("Soda's DuckDB 1.0.0 cannot read openledger_prod.duckdb — the
environments have drifted"), not a raw storage exception, exit code 1.
Restored the real file and re-ran clean. This is a real check, not a
formality — see below for what it would have caught.

**H3.1 requirement 3 — test the lock behavior, don't design around it.**
Started a long-lived DuckDB connection against the prod file (simulating a
dbt build in progress) and launched a Soda scan concurrently. Result:
**immediate, loud failure, not a hang** — DuckDB's own file lock rejects
the second connection within about a second:
`IO Error: Could not set lock on file ... Conflicting lock is held ... by
PID <n>`, Soda CLI exit code **3**. This is the actual failure signature
Phase 6's scheduler needs to recognize if a Soda scan and a dbt build ever
overlap: fast and explicit, not silent corruption or an indefinite hang.
Confirms the sequential design (Soda runs only after `dbt build`
completes) is sufficient — no locking/retry logic needed, since the
failure mode if the sequencing is ever violated is safe and legible.

**A second, independent instance of the exact same bug class already
found in C3.3 — this time inside Soda's own environment.** While designing
the "row-count volume per day" check, the check needed "today" as an
anchor. Tried DuckDB's own `current_date` first, and found it returns
`2026-08-22` regardless of session `TimeZone` setting — confirmed directly
by setting the session's `TimeZone` to `UTC`, then back to
`America/New_York`, and getting the identical wrong date both times.
Compared against the main venv's DuckDB 1.5.5, where changing `TimeZone`
*does* change `current_date` (as established in C3.3's `is_settled` fix).
**`current_date`'s session-TimeZone-awareness is itself version-dependent
in DuckDB, verified empirically between 1.0.0 and 1.5.5** — a real,
concrete difference between exactly the two DuckDB versions this project
now runs side by side. Fixed the same way as C3.3: never call
`current_date`/`current_timestamp` inside a Soda check at all. Instead,
`quality/run_soda.py` computes "today" once, explicitly, in Python via
`zoneinfo` (anchored to `America/New_York`, matching
`created_date_timezone`), and passes it into every check as an explicit
Soda scan variable (`-v today_ny=...`), substituted as `'${today_ny}'::date`
in the check SQL — no check ever depends on either DuckDB session's notion
of "now." Also surfaced a second, unrelated real thing while calibrating
the row-count check: the most recent 1-2 days of `created_date` are
reliably under-populated (2026-08-20 showed 345 rows vs. a ~10,500/day
baseline) — a **publish-lag effect already known from Phase 1**, not a
new defect — so the check anchors to `today_ny - 2`, not `- 1`, to land
reliably past that lag window.

**The six distributional checks**, implemented in
`quality/soda/checks/distributional_checks.yml` as Soda "failed rows"
checks (an explicit SQL query returns a row only when a value is outside
its band — zero rows means pass), every threshold traced to a measured
value across the full 24-month history:

| Check | Measured historical range | Chosen band |
|---|---|---|
| Row-count volume, most recent complete day | 6,216 (2025-12-25, Christmas) – 22,805 (2026-02-24, a snowstorm) | 4,000 – 28,000 |
| Missing-coordinate rate, trailing 30 days | 0.89% – 5.29% (monthly, 24 months; high end is the March 2026 pothole surge — C2.8 Q5) | 0.5% – 7.0% |
| `resolution_hours` median / p90, recently-settled cohort | median 4–26h, p90 313–533h (monthly cohorts) | median 2–35h, p90 250–600h |
| Closure rate at the 45-day settlement boundary | 94.07% – 99.16% (monthly cohorts; low end is the newest cohorts, consistent with completeness still climbing past day 45 per `docs/findings.md`) | 90% – 99.9% |
| Top-10 complaint type month-over-month share delta | max 9.79pp (NOISE - RESIDENTIAL), 8.94pp (HEAT/HOT WATER, the known seasonal mover) | fail if any exceeds 15pp |
| Agency month-over-month share delta | max 10.37pp (HPD, heating-season driven), 8.77pp (NYPD) | fail if any exceeds 15pp |

Every band is set with deliberate margin beyond the observed extremes,
because the observed extremes are themselves legitimate, explained events
(a holiday, a snowstorm, a known seasonal mover) — this layer's job is to
catch something genuinely outside history, not to re-flag events already
understood. Distinguishing "outside this generous band" from "a recurring
expected swing" with more precision is exactly what C3.5's detectors are
for, not this layer.

**Result**: `.venv-soda/bin/python quality/run_soda.py` → compatibility
assertion passes, then **6/6 checks PASSED**, against the real prod build,
scoped to the marts layer as designed in C3.1.

**Post-interruption re-run (2026-08-22, after the mid-session laptop shutdown)**:
5/6 PASSED, 1/6 FAILED (the row-count volume check) — bronze had gone stale
(last ingested row 2026-08-20 01:50:51, no incremental run since) by the time
this check next ran, so the most-recent-complete-day figure legitimately fell
outside the band. Recorded in `docs/metrics.md`; not a check defect, and not
corrected here — it is the check doing its job against genuinely stale input.

#### H3.1 confirmation (2026-08-22, all three requirements tested individually)

**(a) Both requirements files pinned, both DuckDB versions documented.**
Confirmed by inspection: `requirements.txt` pins `duckdb==1.5.5`;
`requirements-soda.txt` pins `duckdb==1.0.0`; `docs/versions.md`'s Phase 3
table names both explicitly. No new testing needed — this was already true.

**(b) Compatibility assertion fires with an explicit drift message — tested,
not just read.** The real `dbt/target/openledger_prod.duckdb` (596 MB) was
backed up, then overwritten with a plain-text file to force
`assert_read_compatibility()`'s `try` block to fail. Result:

```
ERROR: Soda's DuckDB 1.0.0 cannot read openledger_prod.duckdb — the environments have drifted.
This file was built by the main project venv's dbt-duckdb (see requirements.txt for its pinned duckdb version); Soda reads it from a separate venv pinned to duckdb==1.0.0 (requirements-soda.txt), a compatibility verified empirically for today's specific version pair (docs/decisions.md, C3.1) and not guaranteed across an upgrade of either pin.
Original error: IOException: IO Error: The file "...openledger_prod.duckdb" exists, but it is not a valid DuckDB database file!
exit code: 1
```

Fires immediately, names the file, names both pinned versions, exits nonzero.
The real file was restored from backup immediately after and its row count
re-verified (7,533,132 — unchanged).

**(c) Lock-contention behavior — no prior evidence existed; tested now.** A
DuckDB connection was opened against the real prod file with `read_only=False`
from the main venv (DuckDB 1.5.5, simulating a `dbt build` holding the file),
held open across an explicit transaction, then `quality/run_soda.py` was run
concurrently from the Soda venv (DuckDB 1.0.0, `read_only=True`). Result:

```
ERROR: Soda's DuckDB 1.0.0 cannot read openledger_prod.duckdb — the environments have drifted.
Original error: IOException: IO Error: Could not set lock on file "...openledger_prod.duckdb": Conflicting lock is held in ...Python (PID 7712) by user aadarsh_praveen. See also https://duckdb.org/docs/connect/concurrency
exit code: 1 (0.065s — fails immediately, does not block or retry)
```

After the write lock was released 20s later, an unmodified re-run of
`quality/run_soda.py` succeeded normally (5/6 PASS, same result as the
concurrent-free baseline above) — confirming the failure was purely the lock,
not file corruption from the concurrent access attempt.

**A real gap found by this test, not previously known**: DuckDB's file
locking is exclusive even against a read-only opener — `read_only=True` does
not grant a reader access while another process (even a different DuckDB
version, even one holding a read-write connection but not actively writing)
has the file open. Practically: **a `dbt build` running at the same time as
a scheduled Soda scan (Phase 6's cron) will make the Soda scan fail outright**,
not queue or degrade gracefully. `assert_read_compatibility()`'s error
message is also **misleading in this specific case** — it always prints "the
environments have drifted" regardless of cause, even though the lock failure
has nothing to do with version drift. The underlying DuckDB message (which
does correctly name the real cause, "Conflicting lock is held...") is
appended and is the only way to tell the two failure modes apart today. Not
fixed here per this session's "report, don't work around" instruction — this
is a real Phase 6 scheduling constraint (Soda must not run concurrently with
`dbt build`, and the wrapper's message should eventually distinguish the two
cases) to carry forward, not a Phase 3 blocker.

### C3.5 — The operational detectors

Proposed here for **H3.2** approval, per phase-3.md — not yet wired into
`fct_data_quality_checks` (that's C3.6, after approval). All four
backtested against the full 24-month history before any threshold was
finalized, per this phase's own standing rule that an untuned detector
shipped as if it worked is not acceptable.

#### A correction to a Phase 2 finding, found while calibrating detector (a)

Before the detector itself: investigating what actually distinguishes the
DHS founding case from ordinary agency activity surfaced something that
revises how that finding should be understood — worth stating plainly
before describing the detector built on top of it.

**What Phase 2 concluded**: 11,372 2024-created rows, `status='Closed'`,
null `closed_date`, all sharing one `:updated_at` *date* (2025-12-26), 99.9%
DHS, 94% concentrated in two mobile-outreach templates — read as DHS
performing a one-off administrative bulk-closure sweep on that date.

**What this investigation found, checking more carefully**: grouping by
the *exact* `:updated_at` timestamp (not just the date) shows DHS's own
rows are spread across dozens of distinct sub-second timestamps that day
(the three largest are 125, 123, and 121 rows apiece) — not one shared
instant. Widening the lens to *every* agency on 2025-12-26 shows the same
thing, at far larger scale: NYPD, HPD, DOT, DSNY, DEP, DPR, DOHMH, DOB —
every agency — shows a tight, synchronized burst of `:updated_at` activity
between roughly 13:25 and 13:52 that day, in ~2-3 second increments, each
carrying hundreds to thousands of rows per agency. This is not a DHS-
specific event. It has exactly the signature of the **December 2025
Socrata migration already documented in Phase 1** — a platform-wide bulk
touch of `:updated_at`, not a targeted administrative action by one
agency.

**What's still true, and still DHS-specific**: checked whether the
"`Closed` + null `closed_date`" pattern itself appears anywhere outside
DHS, across the *entire* dataset, not just 2024. It does not — 11,366 of
11,372 such rows (99.9%) are DHS; the only other agency showing any of
this pattern at all is DSNY, at 6 rows. So the underlying data anomaly
(thousands of DHS rows stuck in a closed-but-undated state, resolved via
two templated outreach-team phrases) is real and genuinely DHS-specific —
it just didn't get *created* on 2025-12-26 as a discrete sweep. It's more
likely these rows had already accumulated in this closed-without-a-date
state over time, and the Dec-2025 platform-wide migration event is simply
what touched their `:updated_at` (along with essentially everything else's)
on that date, making them *appear* to be a single synchronized event when
grouped only by date. The 94%/98.8% template concentration is real and is
still the useful signal — the "single shared timestamp" framing was the
part that didn't hold up. `docs/findings.md`'s bulk-closure section should
be read with this correction in mind; noting it there directly rather than
silently revising the earlier text.

This also explains, concretely, why phase-3.md's literal detector
definition ("N+ requests from one agency share a **single** `:updated_at`
value") needed adjusting: applied literally (exact-timestamp grouping), it
would not even surface DHS's own event as one group. **Detector (a) below
groups by (agency, calendar day of `:updated_at`), not exact timestamp** —
the closest faithful reading of the spec that actually catches the case
it's named for.

#### (a) Bulk-closure detector — ORIGINAL DESIGN, REJECTED at H3.2

**Rejected on review (H3.2, 2026-08-22).** Kept below as the historical
record of why, per this project's practice of correcting rather than
silently deleting a superseded finding. **See "(a) REDESIGNED" immediately
after this subsection for the approved replacement.**

**Why rejected**: the correction above ("grouping by date instead of exact
timestamp turned accumulated state into an apparent event") directly
undermines this design. The DHS backlog is a persistent, date-free
condition — the December 2025 migration didn't create it, it only stamped
it with a shared date. A detector defined as `group by (agency,
date(updated_at))` therefore (1) finds nothing in an ordinary period,
because the anomaly it's meant to catch has no date signature of its own,
and (2) would false-positive on *any* agency's own accumulated backlog the
next time a platform-wide migration (or any of the recurring mass-touch
nights documented in detector (e) below) happens to stamp that agency's
rows — reporting the migration's date as if it were a fresh bulk-closure
incident, when the real, persistent condition long predates it. A detector
that only fires when something *else* touches the data, and is silent
otherwise, is not detecting bulk closure — it's detecting mass touches,
which is what (e) is for instead.

**Original definition (for the record)**: among rows where `status =
'Closed'` **and `closed_date` is null** (not "any closed row" — see below
for why this restriction matters), group by `(agency, date(updated_at))`.
Fire when a group has **≥100 rows** and its **top-3 resolution_description
templates cover ≥90%** of the group.

**Why the `closed_date is null` restriction, calibrated first**: grouping
by `(agency, date(updated_at))` over *all* closed rows (not just the
undated ones) was tried first and rejected — HPD alone showed a
441,839-row group on 2026-08-20 (a separate, freshly-discovered mass
`:updated_at` touch event, evidently a recent/ongoing instance of the same
platform-wide phenomenon, spanning **13 of 14 agencies simultaneously** —
see the finding below), and NYPD routinely produces same-day groups of
6,000-12,000 rows at 65-85% template concentration simply from its normal
nightly batch-closure process. Neither is a "bulk-closure sweep" in the
sense that matters; both would be false positives under an all-closed-rows
definition. Restricting to `closed_date is null` removes essentially all
of this noise at the source, because it isolates the *actual* anomaly (a
close without a resolution date) rather than the coincidental timestamp
clustering every agency's ordinary batch processing already produces.

**Calibration, on real data, both thresholds checked against the natural
distribution, not assumed**:
- Grouping `(agency, date(updated_at))` restricted to `closed_date is
  null`, at **any** size ≥15 rows, across the full 24-month history:
  exactly **one** agency/day exceeds it — **DHS, 2025-12-26, 17,356 rows**
  (this is larger than Phase 2's 11,372 because it isn't restricted to
  2024-created rows). The only other result at all is **DSNY, same date,
  38 rows** — an order of magnitude smaller, and on the same
  already-explained migration date, not an independent event. The chosen
  floor of 100 sits with wide margin above the 38-row noise floor and
  wide margin below DHS's 17,356.
- Template concentration for the DHS group: top 3 templates cover 12,509 +
  3,836 + 796 = 17,141 of 17,356 rows = **98.8%**. The chosen 90% bound has
  comfortable margin under this.

**Backtested result — directly answering criterion 6**: **exactly one
firing across the full 24-month history.** No other sweeps exist under
this definition. Reported as ruled out, with the specific evidence above,
not merely asserted.

#### (a) REDESIGNED — Agency-level closed-without-date rate anomaly (approved H3.2)

**Definition**: per agency, the rate of `status = 'Closed'` rows with a
null `closed_date`, as a share of that agency's total closed rows. No
date grouping anywhere — the metric is agency-level and time-free by
construction, which is what makes it immune to the failure mode above:
a mass `:updated_at` touch changes *when* a row was last touched, never
*whether* it has a `closed_date`, so this metric cannot be moved by one.

**Cross-agency distribution, measured against the full 24-month history,
via `fct_service_requests` joined to `dim_agency`** (all 16 agencies):

| Agency | Closed rows | Closed + null `closed_date` | Rate |
|---|---:|---:|---:|
| **DHS** | 100,377 | 17,356 | **17.2908%** |
| DSNY | 679,122 | 45 | 0.0066% |
| NYPD | 3,409,758 | 3 | 0.0001% |
| all other 13 agencies | — | 0 | 0.0000% |

**Threshold, derived from the measured variance of the other 15
agencies, not a round number**: excluding DHS, mean = 0.000448%,
population stdev = 0.001651% (n=15). Threshold = mean + 5·stdev =
**0.008705%** — chosen over the more conventional 3·stdev (0.005402%)
specifically because 3·stdev sits *below* DSNY's own observed maximum
(0.0066%), which would make DSNY a marginal false positive; 5·stdev
clears DSNY's actual observed rate with real margin (0.0087% vs.
0.0066%) while remaining **~2,000x below DHS's rate** (17.29% vs.
0.0087%) — no realistic choice of variance-derived margin changes
whether DHS fires.

**Backtest 1 — per-cohort-month rate, a genuine new finding, not
anticipated**: computing the same rate per `(agency, created_date month)`
across all 25 cohort-months shows DHS's rate is **not currently
persistent as an ongoing operational condition** — it fires (>threshold)
in exactly the 10 cohort-months from **2024-08 through 2025-05**
(rates 43-56% of that month's DHS closures), then drops to **exactly
0.0000% every single cohort-month from 2025-06 onward** (15 consecutive
months, 0 new closed-without-date rows created in any of them). Zero
non-DHS agency ever exceeds the threshold in any cohort-month. **This
means the underlying anomaly is a frozen historical backlog created
between Aug 2024 and May 2025, not a live, ongoing DHS process** — a
materially different, more precise characterization than "DHS has this
problem," and one the original date-grouped design could never have
surfaced.

**Backtest 2 — simulated monthly production scan, answering H3.2's actual
question ("does it fire in every period")**: a backlog that is frozen at
the row level does not disappear from a *cumulative* scan just because it
stopped growing — nothing in this pipeline goes back and fixes or deletes
these rows. Simulating a monthly-scheduled monitor (evaluate DHS's
cumulative rate over all rows with `created_date` before each of 25
month-end cutoffs, Sep 2024 through Sep 2026) shows DHS's cumulative rate
**exceeds the 0.008705% threshold in all 25 of 25 simulated monthly
scans** — declining slowly from 55.5% (Sep 2024, when the backlog was
still a large share of DHS's then-small closed-row count) to 17.3% today
(as DHS's ordinary, undated-free closures accumulate in the denominator),
but never close to threshold. **Confirms the persistence you'd expect: a
production monitor deployed today would have fired every single month
since the backlog first existed, and would continue to fire every month
going forward until the backlog is remediated** — even though the backlog
itself stopped *growing* 15 months ago (Backtest 1's finding).

**Correction (2026-08-23, found implementing this detector in code —
`dq_detector_undated_closure_rate.sql`):** "zero non-DHS firings" above was
**wrong**, and was never actually checked for this cumulative backtest — it
was carried over, in error, from Backtest 1's per-cohort-month result
(which genuinely is zero) without re-verifying it for Backtest 2's
different, cumulative methodology. Per this project's own rule 4 ("when
the code disagrees with the prose, the code is authoritative and the
journal gets corrected"): the code, run against all 16 agencies rather
than DHS alone, shows **DSNY fires in 10 of the 25 simulated scans**
(2025-04 through 2026-01), at rates 0.0091–0.0118% — just above the
0.008705% threshold. Full cause, from DSNY's own cumulative history: its
undated-closure row count grows from 1 to 45 over the full 24 months while
its closed-row denominator grows much faster, so its rate rises just past
threshold for a 10-month stretch in the middle of the window (peak
0.0118%, Jun 2025) and then falls back under it from Feb 2026 onward as
the denominator keeps growing while the numerator is essentially flat (44
→ 45 across the last 7 scans). **DSNY is not currently firing** — the
latest (Sep 2026) scan shows 0.0066%, comfortably under threshold — and
nothing about its pattern resembles DHS's (no bounded backlog narrative
fits; it just has a thin, transient margin over the threshold). This is a
genuine, if minor, limitation of the 5·stdev threshold under the
cumulative-scan methodology specifically (not the per-cohort-month one,
where it correctly never fires) — the threshold clears DHS by ~2,000x but
DSNY's ambient noise ceiling (0.0118%) is only ~1.4x the threshold
(0.0087%), not a comfortable margin. Not re-tuned here (H3.2 already
approved this threshold and re-tuning post-hoc without new review would be
exactly the "quietly adopt the code's number" this rule exists to
prevent) — flagged as a known limitation for a future threshold review,
and DSNY's transient historical firings should NOT be read as 10 separate
incidents requiring investigation; they are one continuous, now-resolved,
sub-threshold-margin condition.

**Second correction (2026-08-24, found answering a user question about
the above — the DSNY finding was itself wrong, not just DHS's "0 non-DHS
firings" claim).** The question that exposed it: if DSNY's cumulative
rate *ends* at 0.0066% (comfortably under the 0.008705% threshold), why
would it have been *above* threshold at any earlier point, given the
threshold above was computed once from the full-history distribution as
it stands *today*? Tested directly: recomputed the threshold
**independently at each of the 25 scan_months, using only data available
as of that same month** (no look-ahead into later months), instead of one
threshold computed from today's endpoint and applied to all 25 historical
checkpoints. Result: **DSNY fires in 0 of 25 scans under a point-in-time
threshold — every one of its 10 "firings" above was an artifact of
comparing a 2025 rate against a 2026-computed threshold**, not a real
excursion. DHS is unaffected (still 25/25; its margin is large enough at
every single historical checkpoint, not just today, for the look-ahead
distinction to matter). Every other agency also fires 0/25 under either
threshold. **`dq_detector_undated_closure_rate.sql` fixed** to compute
the threshold per scan_month rather than once; re-verified against this
corrected methodology, not just asserted.

**What this means for the two "genuine limitation" framings above**:
neither the "5·stdev may be too tight" concern nor the "DSNY has a thin
transient margin" finding holds up — both were built on a threshold that
was never a fair comparison for the months being evaluated. DSNY's own
rate trajectory (rising to a real peak of 0.0118% at 2025-06, then
declining to 0.0066% today) is real and unchanged; what was wrong is
comparing it to a threshold that hadn't been computed yet at the time.
5·stdev, evaluated fairly (point-in-time), clears every non-DHS agency at
every historical checkpoint with comfortable margin (DSNY's closest
approach was still meaningfully under its own contemporaneous threshold —
e.g. 0.0118% vs. 0.01657% in June 2025) — no evidence it needs widening.
This is the "framing mismatch" branch, not the "genuine finding" branch:
the detector's *design* (mean + 5·stdev, agency-level, date-free) was
correct all along; only its *implementation* (a static threshold applied
retroactively) was buggy.

**Report requirement satisfied**: the distribution table above makes DHS
visible as a rate sitting ~2,000x above the derived threshold and
~38,600x above the mean of the other 15 agencies (17.29% vs. 0.000448%)
— a distribution, not a binary pass/fail.

#### (b) Composition-drift detector

**Definition**: for each of the top 15 complaint types by volume and each
calendar month, compare that month's share of total volume to the **same
calendar month one year earlier** (not a trailing average, and not
month-over-month — both would fire on every recurring seasonal swing, the
exact failure mode phase-3.md warns against). Fire when the year-over-year
share delta exceeds **5 percentage points**. Months with no same-calendar-
month prior-year observation (the dataset's first 12 months, Aug 2024 -
Jul 2025) are skipped — there is nothing to compare them against yet, an
honest scope limitation rather than a fabricated baseline.

**Why year-over-year, validated on a real contrasting pair already in the
data, not hypothetically**: HEAT/HOT WATER swings from ~1% to ~21-23% of
monthly volume every winter — the largest raw seasonal amplitude of any
complaint type — yet its year-over-year deltas (comparing e.g. Jan 2026 to
Jan 2025) are consistently small: 3.63pp, 1.70pp, and similar, because the
*same* swing already happened the prior year and cancels out. This is a
real recurring pattern that has now been observed twice in the dataset
(winter 2024-25 and winter 2025-26), and the detector correctly does not
fire on it. STREET CONDITION's March 2026 pothole surge, by contrast,
shows an 5.88pp YoY delta (8.38% vs. 2.50% the prior March) — a real,
first-time deviation, correctly above threshold.

**A genuine second finding, not anticipated going in**: the same backtest
also fired on **NOISE - RESIDENTIAL, January 2026, a −12.1pp YoY delta**
(8.25% vs. 20.34% in January 2025) — larger than the STREET CONDITION
calibration case. Investigated rather than dismissed: the entire
divergence traces to one descriptor, `LOUD MUSIC/PARTY`, which hit 56,133
rows in January 2025 against a `NOISE - RESIDENTIAL` descriptor baseline of
roughly 12,000-20,000/month in every neighboring month (Dec 2024: 32,455;
Feb 2025: 13,756) — January 2025 itself is the true outlier, not January
2026, which fits its own neighboring months smoothly. Checked whether this
was a narrow New Year's-specific spike (a plausible, explicable cause) by
looking at daily counts Dec 28 - Jan 4: elevated across the whole week
(2,400-4,600/day), not concentrated on Jan 1-2 specifically, so a clean
"New Year's noise" explanation doesn't fully hold up. **Reported honestly
as found-but-not-fully-root-caused** — recorded in `docs/findings.md`
rather than left only here, since a documented open question is more
useful to Phase 4/5 than a confident-sounding guess.

**Backtested result**: 15 types × 13 eligible months = 195 possible
evaluations. **Correction (2026-08-23, found implementing
`dq_detector_composition_drift.sql`)**: the code produces **194** actual
evaluations, not 195 — the 195 figure assumed every top-15 type has
nonzero volume in every eligible month, which wasn't individually checked.
The one missing cell is (WATER SYSTEM, Aug 2026): bronze's most recent
month is a partial month (data through 2026-08-20 only, per the ingestion
interruption already documented), and WATER SYSTEM genuinely had zero
recorded complaints in that partial window, so there is no row to compute
a share from — not a defect, just a partial-month edge case the original
back-of-envelope multiplication didn't account for. Firing count and
identity are unaffected: **2 firings** (STREET CONDITION Mar 2026,
+5.88pp; NOISE - RESIDENTIAL Jan 2026, −12.10pp) — a ~1.03% firing rate
(2/194). HEAT/HOT WATER's genuine, large, twice-recurring seasonal swing
does not fire, which is the specific behavior this detector exists to get
right.

**Honest limitation**: the "don't fire on a recurring pattern" property is
validated using HEAT/HOT WATER (a pattern that has now recurred and been
correctly suppressed), but the dataset spans only one full pothole season
(March 2026) — there is no second STREET CONDITION spring yet to confirm
this *specific* pattern gets suppressed the second time. That test becomes
possible once the dataset reaches March 2027; flagged here so it isn't
forgotten rather than quietly assumed to already be proven.

#### (c) Vocabulary-drift monitor

**Definition**: a notification, not a pass/fail check, per phase-3.md.
Reports every `complaint_type`, `descriptor`, or `agency` value whose
first-seen `created_date` falls within the current scan's evaluation
window, with its first-seen month and total volume to date.

**Backtested against the full history** (excluding the initial 2024-08
backfill month itself, which trivially "introduces" the entire starting
vocabulary):
- **New agencies: exactly 2** in ~23 months — `OOS` (first seen 2025-09,
  3,951 rows to date) and **`NYC311-PRD`** (first seen 2026-03, 371 rows
  to date). `OOS` was already a known, legitimate small agency (seen in
  the C2.8 agency table). **`NYC311-PRD` looks like a 311-system routing
  or placeholder code, not a real city agency** — worth flagging as
  exactly the kind of thing this monitor exists to surface rather than
  silently accepting as a new agency the same way a real one would be
  accepted. Recorded in `docs/findings.md` as an open question, not
  resolved here.
- **New complaint types**: 17 of ~23 months show at least one new value —
  heaviest right after backfill (3-5/month in Sep-Dec 2024, as the
  vocabulary "fills in"), settling to 0-1/month afterward. Several are
  clearly genuine, explicable additions — `CANNABIS RETAILER` (first seen
  2025-09, 3,951 rows — NYC's cannabis retail regulation stood up around
  then) is a clean, real example of legitimate vocabulary growth, not
  drift-as-defect.

**No conventional false-positive rate applies here** — every "firing" is a
true positive by construction (a new value either appeared or it didn't);
what's reported instead is firing *frequency*, which is deliberately high
for complaint types (311's vocabulary genuinely does evolve continuously,
per CLAUDE.md's own documented trap) and appropriately low for agencies (2
in 23 months) — a useful signal specifically because the two rates differ
so much.

#### (d) Settlement-completeness tracker

**Definition**: recompute completeness-at-45-days (the same measurement
behind H2.2's cutoff) for the most recent eligible cohort months on a
rolling basis, and compare against the documented ~93% baseline.

**A methodological trap found while building the "rolling" part,
worth stating explicitly**: recomputing completeness for the *most
recent* few cohort months naively (anything with `status='Closed'` as of
today) gives a trivially-inflated, meaningless number for any cohort under
about 90 days old — e.g. August 2026 (barely 3 weeks old at measurement
time) showed "100% completeness at 45 days," which is a mathematical
artifact, not a real result: a cohort that hasn't existed for 45 days yet
can only contain rows that closed fast, by definition, so *of course*
100% of its (small) closed subset closed within 45 days. **The tracker
must only evaluate cohorts old enough for the measurement to mean
anything** — reused Phase 2's own ≥90-day-old criterion for exactly this
reason, and excludes the already-documented contaminated cohorts (the
Dec-2025 migration and Jan-2026 spike) the same way Phase 2 did.

**Result, run today**: the three most recent eligible clean cohorts are
still **Feb/Mar/Apr 2026 — identical to Phase 2's own findings** (92.06% /
92.98% / 93.92%), since less than one cohort-month's worth of wall-clock
time has passed since that measurement. This is a useful confirmation of
reproducibility (same methodology, same inputs, same answer) rather than
new information — the tracker will report a genuinely new set of cohorts
the next time a full month has aged past the 90-day mark.

**Proposed thresholds**: warn if any tracked cohort's completeness at 45
days falls below 90%; fail if below 85%. Both sit with real margin under
the observed 92-94% range, wide enough not to fire on ordinary
month-to-month noise in that range, tight enough to catch a genuine drift
before it meaningfully invalidates the cutoff.

#### (e) Mass metadata-touch detector (proposed at H3.2, per review feedback)

**Why this exists as a separate detector, not folded into (a)**: (a)'s
redesign is deliberately date-free and agency-level, because that's what
makes it immune to being triggered by a mass touch. But a date-grouped,
cross-agency, `:updated_at`-clustering signal is itself a real and useful
thing to monitor — it's exactly the phenomenon that corrupted the original
(a) design, and per `docs/decisions.md`'s C1.7d finding and the backtest
below, it recurs far more often than the two or three incidents so far
individually named. Phase 6's planned `:updated_at`-based freshness
monitoring needs to know when this is happening so it isn't misread as
genuine data staleness or genuine new activity. Kept fully separate from
(a) so the two concerns — "does this agency have a persistent undated-
closure problem" vs. "is tonight's `:updated_at` activity a normal
platform touch or an outlier" — are never conflated in one signal again.

**Definition**: group `fct_service_requests` by `(agency,
date_trunc('hour', updated_at))`. For each hour bucket, compute the
count of distinct agencies touched and the total row count. A bucket is
*eligible* (candidate for firing) when **≥13 of the dataset's 16 agencies**
are touched in that hour (roughly 80%, chosen because this is exactly the
range every ordinary night already sits in — see below). Among eligible
buckets, **fire when total rows exceed a threshold derived from the
measured variance of the ordinary-night distribution**, not from the
eligibility count itself (see why below).

**Calibration, on real data — the eligibility bar alone is not a useful
signal**: querying every hour bucket across the full 24-month history
finds **180 buckets (of ~730 possible nights) already meet the ≥13-agency
bar** — this cross-agency nightly touch is not rare, it is the dataset's
normal nightly rhythm (matches the ~01:33 UTC recurring signature already
found in Phase 1's C1.7d). Row counts in these 180 ordinary buckets range
4,825–50,337 (excluding the two most extreme), median 11,506, mean 11,667,
population stdev 4,392. **Using agency-count alone as the fire condition
would make this detector fire on literally every normal night** — the
discriminator has to be volume, not breadth.

**Threshold**: mean + 3·stdev of the ordinary-night distribution (computed
excluding the two most extreme nights, to avoid the extremes inflating
their own detection threshold) = **24,842 rows**.

**Backtested result**: of the 180 eligible nights across 24 months,
**6 exceed 24,842 rows**:

| Date | Agencies | Rows | Dominant agency | Character |
|---|---|---:|---|---|
| 2025-12-26 | 15 | 4,347,980 | none (evenly spread) | The Dec 2025 Socrata migration — genuinely platform-wide |
| 2025-12-31 | 15 | 50,337 | NYPD (26,676, 53%) | One agency's large batch night |
| 2026-02-16 | 13 | 121,863 | HPD (114,590, 94%) | One agency's large batch night |
| 2026-04-28 | 13 | 31,899 | DOHMH (21,269, 67%) | One agency's large batch night |
| 2026-07-15 | 13 | 26,717 | NYPD (8,999, 34%, more evenly spread) | Borderline, mixed |
| 2026-08-20 | 14 | 522,196 | HPD (441,839, 85%) | One agency's large batch night — the original C3.5 finding |

**Firing rate**: 6 of 24 months (one per month at most; no month has more
than one firing) — a ~25% monthly firing rate, ~0.8% of all nights. Not
too noisy to be useful (criterion 10): the vast majority of nights (724 of
730) do not fire, and the 6 that do include the one confirmed, independently
corroborated platform event (the Dec 2025 migration) plus five real,
inspectable volume outliers, not arbitrary noise.

**Full detail on the six events, including the Phase 6 freshness-
monitoring consequence, in `docs/findings.md`.**

#### Summary for H3.2

| Detector | Threshold(s) | Backtested firings / 24 months (as reproduced in code, 2026-08-23) | Notes |
|---|---|---|---|
| (a) REDESIGNED — agency-level rate | rate > mean + 5·stdev of non-DHS agencies, computed POINT-IN-TIME per scan (not a single static value — see the second correction above) | DHS: 25/25 monthly cumulative scans (persistent). All 15 other agencies: 0/25 (DSNY's apparent 10/25 was a look-ahead artifact in the threshold computation, fixed — not a real excursion) | Original date-grouped design rejected at H3.2; threshold's look-ahead bug found and fixed 2026-08-24 |
| (b) Composition-drift | \|YoY share delta\| > 5pp, top 15 types | 2 of 194 evaluations (corrected from 195 — one type-month cell is empty due to the partial Aug 2026 month) — STREET CONDITION Mar 2026; NOISE-RESIDENTIAL Jan 2026 | Correctly suppresses HEAT/HOT WATER's much larger but twice-recurring seasonal swing |
| (c) Vocabulary-drift | none (notification only) | 2 new agencies, 17/23 months with ≥1 new complaint type | `NYC311-PRD` flagged as suspicious, not a confirmed defect |
| (d) Settlement-completeness | warn <90%, fail <85% at day 45, ≥90-day-old cohorts only | 0 (Feb/Mar/Apr 2026 at 92-94%, matching Phase 2; May 2026 newly eligible as of this run at 95.25%) | Reproduced Phase 2's exact numbers as a validation, not new drift |
| (e) Mass metadata-touch (approved H3.2b) | ≥13/16 agencies in one hour AND rows > mean + 3·stdev of ordinary nights (24,842) | 6 (1 platform-wide migration + 5 single-agency batch-volume nights) | Replaces (a)'s original date-grouped design; feeds Phase 6 freshness-monitoring caveat |

**All five detectors are now implemented as dbt models**
(`dbt/models/marts/quality/dq_detector_*.sql`), each computing the full
historical backtest (not just a current-day snapshot) so the numbers above
are re-verifiable by querying the models directly, not just asserted here.
The two corrections above were found by doing exactly that — running the
code and diffing its output against this journal, per this project's own
rule that the code is authoritative when the two disagree.

### C3.7 — The DHS exclusion decision

**Decision: both a flag and a stated exclusion, per phase-3.md's own
framing of the choice as non-exclusive.**

**The flag**: `is_undated_closure` (boolean), added to
`int_request_resolution` and propagated to `fct_service_requests` —
`status = 'Closed' and closed_date is null`. Generic (not DHS-specific in
its definition — any agency could trigger it), but currently 99.9%
concentrated in DHS by measurement, not by construction. Contract-enforced
(`not_null`) at both layers.

**Auditability — the affected population, stated exactly, not left as a
magic filter**: **17,356 rows**, agency = DHS, `created_date` between
**2024-08-19T00:21:07** and **2025-05-06T01:18:06** (America/New_York) —
the exact bounded window established in the C3.5(a) redesign's cohort
backtest. Any consumer can reproduce this population directly:
`select * from fct_service_requests f join dim_agency a using (agency_key)
where a.agency = 'DHS' and f.is_undated_closure`.

**The quantified SLA delta — DHS's closure rate among settled requests,
computed both ways, live against the current prod build**:

| Measure | Settled population | Closed (numerator) | Closure rate |
|---|---:|---:|---:|
| **Including** the undated-closure rows (current default: `is_closed` already requires a non-null `closed_date`, so these rows count as "settled but not closed" — indistinguishable from a genuinely still-open request) | 96,990 | 78,531 | **80.97%** |
| **Excluding** the undated-closure rows entirely (removed from both numerator and denominator — treated as an administrative category outside the scope of a response-time SLA) | 79,634 | 78,531 | **98.61%** |
| For contrast: naive reading using raw `status = 'Closed'` (no `closed_date` requirement at all — counts the backlog as successfully closed) | 96,990 | 95,887 | 98.86% |

**Delta: +17.65 percentage points (80.97% → 98.61%)** — a large,
consequential difference, and in the opposite direction from phase-3.md's
original framing. The original concern was that the sweep might *inflate*
DHS's apparent SLA if included; what the data actually shows is that
**leaving these rows in the settled-but-not-closed bucket makes DHS's
current, real closure performance look ~18 points worse than it is**,
because 17,356 historical rows with no way to compute a resolution time
sit permanently uncredited as "not closed" under the model's own
conservative `is_closed` definition. The naive raw-`status` reading
(98.86%) and the excluding reading (98.61%) land close together — both
treat the backlog as resolved one way or another — which is the more
accurate read of DHS's actual, current-process performance.

**Recommendation for any consumer (Phase 4's semantic layer, the Phase 5
dashboard)**: report DHS's closure/SLA metrics using the **excluding**
reading, with the 17,356-row exclusion and its date range stated
explicitly wherever the metric appears — never silently. The **including**
reading remains available (it's simply "don't filter on
`is_undated_closure`") for anyone who specifically wants to see the
backlog's drag on the raw numbers.

### C3.6 — The DQ scorecard mart

`fct_data_quality_checks` (`dbt/models/marts/fct_data_quality_checks.sql`).
Grain: one row per `(check_name, grain, run_date)`. Materialized
**incremental** (`delete+insert` on that unique key), not a plain table —
re-running the same day replaces that day's rows; a new day adds a new
set alongside every prior day's, so the mart actually accumulates history
across scheduled runs rather than only ever showing "now" (phase-3.md's
explicit requirement: "the dashboard can show trend, not just current
state").

**Four categories, 103 rows on this run** (8 contract + 7 unit + 6
distributional + 82 detector):
- **contract** (8 rows): build-gated — a contract violation would have
  failed the build before this model runs, so every row here is
  definitionally `status='pass'`; the row's presence each run day IS the
  proof.
- **unit** (7 rows): same build-gated logic for the 7 unit tests.
- **distributional** (6 rows): a live, independent SQL recomputation of
  the same 6 checks in `quality/soda/checks/distributional_checks.yml`
  (not a copy of Soda's result — Soda remains primary; this is a second,
  cheap, dbt-native cross-check with its own history). Cross-checked
  against Soda's own live run: both currently report the same 1-of-6
  failure (`row_count_volume`, value 0 vs the [4000,28000] band) for the
  identical reason (bronze is stale post-interruption) — the two
  independent implementations agree, which is itself a small proof the
  SQL port is faithful to the Soda check it mirrors.
- **detector** (82 rows): current-state rows pulled from the five
  `dq_detector_*.sql` models under `dbt/models/marts/quality/` — each of
  which computes the FULL 24-month backtest every run (not just today),
  so every number in the H3.2/H3.2b journal entries is independently
  re-verifiable by querying those models directly, not just asserted in
  prose. Two real discrepancies were found doing exactly that — see the
  corrections in C3.5(a) and C3.5(b) above.

**Acknowledgment mechanism (review note 1)**: `dbt/seeds/quality_acknowledgments.csv`
maps a `(check_name, grain)` to a dated acknowledgment. The scorecard
overrides a would-be `'fail'` to `'acknowledged'` only for a matching row
— currently just DHS's `undated_closure_rate_anomaly` (acknowledged
2026-08-22, the H3.2/H3.2b review date) — carrying the true measured value
(17.2908%) and the acknowledgment date through unchanged, so the
condition stays visible and dated rather than silently suppressed or left
permanently red. A detector that fires 25/25 scans by design (persistent,
not noisy — see C3.5(a)) now reads as one acknowledged, dated, bounded
condition in the scorecard instead of wallpaper.

**Not contract-enforced**, deliberately: `measured_value`'s meaning is
category-dependent (a percentage, a row count, a delta in percentage
points) — a single enforced numeric type can't usefully constrain that
further, and forcing one category's semantics onto all four would be a
worse contract than none. Tested instead with `not_null` on every column,
`accepted_values` on `category` and `status`, and a hand-written grain-
uniqueness test (`assert_dq_scorecard_grain_unique.sql` — no `dbt_utils`
dependency in this project, so a 3-column `unique` combination is asserted
directly rather than via a package macro).

**A self-caught error in the contract category's own numbers, worth
recording**: because contracts are build-gated (no live query naturally
produces "how many columns does this model have"), the 8 contract rows'
`measured_value` were first written as hardcoded literals recalled from
memory rather than re-derived from the schema files — and three of the
eight were wrong (`int_request_resolution` written as 15 instead of 8,
`dim_complaint_type`/`dim_location` as 4 instead of 3, `dim_date` as 10
instead of 11, `fct_service_requests` as 18 instead of 19 — a mix of
stale pre-C3.7 counts and simple miscounts, not one consistent cause).
Caught by cross-checking against a fresh `grep`/`awk` count of the actual
YAML files before shipping, not by a test — which is itself the gap:
**added `assert_dq_scorecard_contract_counts_match_schema.sql`**, which
compares each hardcoded `measured_value` against DuckDB's own
`information_schema.columns` count for that table, live, every build, so
this exact class of silent drift (a column added or removed without the
literal being updated) fails the build going forward instead of quietly
reporting a wrong number forever.

### C3.8 — Quality wired into one command

**`dbt build --profiles-dir . --target prod`, run from the `dbt/`
directory (not `--project-dir dbt --profiles-dir dbt` from the repo root —
verified that fails: `profiles.yml`'s relative `path: 'target/...'`
resolves against the invoking process's cwd, not `--project-dir`, the same
quirk already documented for Soda's `configuration.yml`; tested this
exact failure live before writing the command down here rather than
assuming it would work)** now
runs models, contracts, unit tests, data tests, AND all five detectors AND
the scorecard mart itself, in one command — measured at **13.9s** total
(94 nodes: 10 table models incl. 5 detectors + the scorecard, 3 view
models, 1 seed, 72 data tests, 7 unit tests). The detectors are cheap
(5 models, ~2.2s combined when built in isolation) precisely because each
computes a small, pre-aggregated grain (agency-month, hour-bucket,
type-month, cohort-month — never row-level), not because anything was cut
to make them fast.

**Soda remains a genuinely separate step** — not merged into `dbt build`,
and not because of a mere convenience choice: C3.1's resolution requires
two non-mergeable Python environments (DuckDB 1.5.5 vs. `soda-core-duckdb`'s
hard pin on DuckDB `<1.1.0`), so Soda can only ever run as a second
process, `.venv-soda/bin/python quality/run_soda.py`, invoked after
`dbt build` completes — never during it (H3.1(c)'s lock-contention finding:
Soda hard-fails in ~0.065s if it overlaps a `dbt build` holding the file,
it does not queue). Measured at **2.0s**. Full sequential suite (`dbt
build` then `quality/run_soda.py`): **~16s total** — an order of magnitude
under any Phase 6 scheduling concern, and comfortably allows the two to
run back-to-back rather than needing to overlap.

**One command, two steps, documented as such** — not a false claim of a
single unified command, since that would misrepresent the H3.1(c)
constraint. Run from the repo root:
```
(cd dbt && dbt build --profiles-dir . --target prod) && \
    .venv-soda/bin/python quality/run_soda.py
```

### C3.9 — Journal, metrics, commit

This entry, `docs/findings.md`'s C3.7 and DHS-backlog/mass-touch sections,
and `docs/metrics.md`'s Phase 3 sections carry the full record. Summary of
what changed across C3.6-C3.8, beyond what's already detailed above:

- `dbt/models/marts/quality/dq_detector_*.sql` (5 new models) — every
  detector from H3.2/H3.2b, implemented in code, each computing the
  full 24-month backtest every run.
- `dbt/models/marts/fct_data_quality_checks.sql` (new, incremental) — the
  C3.6 scorecard.
- `dbt/seeds/quality_acknowledgments.csv` (new) — the acknowledgment
  mechanism behind review note 1.
- `int_request_resolution.sql` / `fct_service_requests.sql` — added
  `is_undated_closure` (C3.7).
- `quality/run_soda.py` — fixed to distinguish a lock-contention failure
  from genuine version drift (review's two smaller items).
- `docs/versions.md` — fixed the stale `scripts/soda_scan.py` reference.
- Two journal corrections made from running the code, not asserted in
  advance: detector (a)'s "0 non-DHS firings" (actually DSNY, 10/25,
  now resolved) and detector (b)'s 195-vs-194 evaluation count.
- One self-caught bug: the scorecard's own hardcoded contract column
  counts were wrong in three of eight cases; fixed, and a new test
  (`assert_dq_scorecard_contract_counts_match_schema.sql`) added so this
  can't drift silently again.

#### STOP GATE 3 — verification against phase-3.md's 15 criteria

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Soda conflict resolved | ✅ | C3.1: separate venv, chosen after empirical testing. H3.1(a)(b)(c) each individually tested live (files pinned, drift-message fires, lock-contention behavior characterized) |
| 2 | Contracts across all layers | ✅ | 8 models, 103 columns (updated from 101 by C3.7); deliberate break demonstrated and reverted (C3.2); live-verified every build by `assert_dq_scorecard_contract_counts_match_schema.sql` |
| 3 (load-bearing) | Unit tests cover boundary cases | ✅ | 7 unit tests; 45-day boundary tested at exactly 44/45/46 days; a real timezone bug found and fixed while writing them |
| 4 | Distributional checks running | ✅ | 6 checks, in both Soda and an independent SQL cross-check in the scorecard; both agree on today's 5/6 (bronze staleness, not a defect) |
| 5 | Bulk-closure detector catches DHS | ✅ | Redesigned detector (a): DHS fires 25/25 monthly cumulative scans, ~2,000x the threshold |
| 6 (load-bearing) | Other sweeps found or ruled out | ✅ | Detector (e): 6 mass-touch nights found across 24 months, full detail in `docs/findings.md`. Detector (a): all 15 non-DHS agencies ruled out, 0/25 firings, once a look-ahead bug in the threshold (found 2026-08-24, see the second correction above) was fixed |
| 7 | Composition detector catches STREET CONDITION | ✅ | Confirmed; does not fire on HEAT/HOT WATER's larger recurring swing; honestly caveated as only one pothole season observed so far |
| 8 | Vocabulary monitor works | ✅ | 2 new agencies with first-seen dates/volumes; 17/23 months with new complaint types |
| 9 | Settlement tracker running | ✅ | 4 eligible cohorts, 92.06%-95.25%, all passing |
| 10 (load-bearing) | False-positive rates reported | ✅ | Every detector's firing count stated per agency/month/night; DSNY's apparent firing was investigated on request, found to be a threshold look-ahead bug (not a real excursion), fixed, and re-verified rather than either hidden or left mischaracterized as a "real limitation" |
| 11 | Thresholds approved | ✅ | H3.2 (2026-08-22) and H3.2b (2026-08-22) both recorded |
| 12 | Scorecard mart built | ✅ | 103 rows, 4 categories, incremental (accumulates one row-set per run_date) |
| 13 | DHS SLA delta quantified | ✅ | 17,356 rows, 2024-08-19 to 2025-05-06; 80.97% → 98.61%, +17.65pp |
| 14 | Suite runnable in one command | ✅ | `dbt build` (13.9s) + `quality/run_soda.py` (2.0s), ~16s total, documented as two necessarily-separate steps with the reason why |
| 15 | One atomic commit | ✅ | `c89f11a`, "Phase 3: data quality contracts, unit tests, and operational scorecard"; this clarification (the detector (a) threshold fix) lands in a small follow-up commit rather than reopening/amending it |

**Load-bearing criteria 3, 6, 10 all hold with real depth, not just a
checkmark**: 3 caught a genuine timezone bug during the work itself; 6 and
10 together caught and fixed a genuine look-ahead bug in detector (a)'s
threshold — found not during the original build, but by taking a user's
follow-up question seriously enough to actually re-derive the answer
(point-in-time vs. static threshold) instead of restating the prior,
now-superseded explanation with more confidence.

---

# Phase 4 — Semantic Layer in Open-Source MetricFlow

## C4.1 — Verify MetricFlow on dbt-core + DuckDB (2026-08-23)

Per phase-4.md: this is the phase's gate. Nothing past this point is built
until H4.1 approves the outcome. All five verification steps below were
run for real, not assumed from the plan's claims.

### Step 1 — install, pin, confirm license

`metricflow==0.212.0` was already present as a transitive dependency of
`dbt-core==1.12.3` (used internally for manifest parsing) — but the
package that actually provides a runnable CLI, `dbt-metricflow`, was not
installed. Resolved with `pip install --dry-run dbt-metricflow` first
(not installed blind): clean resolution, zero conflicts against the
existing `dbt-core<1.13,>=1.11` / `dbt-duckdb==1.11.0` pins — installed
into the **same main venv**, unlike Soda (C3.1), which needed a separate
one because of a genuine version conflict. Only 5 small new transitive
deps (`halo`, `log-symbols`, `spinners`, `termcolor`, `update-checker` —
CLI spinner support, non-load-bearing). Installed for real:
`dbt-metricflow==0.14.0`.

**License, confirmed via `pip show`, not assumed from CLAUDE.md's claim**:
both `metricflow` and `dbt-metricflow` report `License-Expression:
Apache-2.0` directly in their package metadata. Pins and full detail in
`docs/versions.md`.

### Step 2 — confirm dbt-core only, no dbt Cloud

Checked the environment directly: no `DBT_CLOUD_*` variable of any kind is
set anywhere. `mf`'s CLI entry point (`dbt_metricflow.cli.main:cli`)
queries directly against dbt-core's own compiled
`target/semantic_manifest.json` and executes generated SQL through
dbt-duckdb's adapter connection (confirmed by reading the actual
traceback when step 4 first failed — the call stack goes straight through
`dbt.adapters.duckdb.connections`, the same code path `dbt build` itself
uses). No network call to any dbt Labs service anywhere in the path.
**Confirmed local-only**, matching CLAUDE.md's "local MetricFlow" framing.

### Step 3 — one trivial semantic model, one trivial metric, queried end to end

**A real prerequisite found before any semantic model could work at
all**: `mf list metrics` initially failed outright — "At least one time
spine must be configured to use the semantic layer, but none were found."
MetricFlow requires a designated time-spine model (a one-row-per-day
calendar) before it will do anything, regardless of what metric is being
tested. Rather than build a redundant spine model, **`dim_date` was
designated as the time spine** (`time_spine: standard_granularity_column:
calendar_date` added to its `_marts.yml` entry, plus `granularity: day` on
the `calendar_date` column) — it already is exactly a one-row-per-day
calendar spanning 2024-08-19 through 2027-12-31, so reusing it is correct,
not a workaround (the same "the full grain already fits, don't rebuild
it" pattern Phase 2 used for `dim_location`'s join key). This is
infrastructure, not an analytical decision — it carries no filter/metric
semantics of its own — so it's kept as a permanent addition, not reverted
with the rest of this section's throwaway work.

**A throwaway verification file** (`dbt/models/marts/_c4_1_verification.yml`,
explicitly labeled as such in its own header, deleted immediately after
this verification) defined one semantic model (`sm_c4_1_verification`,
over `fct_service_requests`, entity `unique_key`, time dimension
`created_date`) and one metric (`request_count_verification`, a bare
count with zero filters) — deliberately the simplest possible thing that
could prove the pipe works, carrying no real analytical decisions that
C4.2/C4.3 would need to inherit or that H4.2 would need to approve.

`mf query --metrics request_count_verification --explain` produced:
```sql
SELECT
  SUM(CASE WHEN unique_key IS NOT NULL THEN 1 ELSE 0 END) AS request_count_verification
FROM "openledger_prod"."main"."fct_service_requests" sm_c4_1_verification_src_10000
```
— correct, sensible SQL, generated from the YAML definition with no SQL
written by hand.

### Step 4 — generated SQL runs and matches a hand-written query

**A second real toolchain quirk found here, not in the plan**: running
the query for real (not `--explain`) initially failed —
`Binder Error: Catalog "openledger_prod" does not exist!` — even though a
direct `duckdb.connect()` to that exact file reports its own catalog name
as `openledger_prod`. Root cause, confirmed by reading the traceback:
`mf` resolves its own DuckDB connection/target **independently** of
whatever target the manifest was last parsed with, defaulting to
`profiles.yml`'s own `target: dev` unless overridden — so the compiled
SQL (parsed with `--target prod`, catalog name `openledger_prod` baked in
as a literal) was being executed against a connection actually opened for
`openledger_dev.duckdb` (catalog name `openledger_dev`), which genuinely
does not contain anything called `openledger_prod`. Not a MetricFlow bug,
a parse-target/runtime-target mismatch. **Fixed by setting `DBT_TARGET=prod`
explicitly** (`mf` takes no `--target` CLI flag; environment variable only)
to match the `dbt parse --profiles-dir . --target prod` invocation that
generated the manifest being queried. Working command, run from `dbt/`:
```
DBT_TARGET=prod DBT_PROFILES_DIR=. mf query --metrics request_count_verification
```

**Result: 7,533,132** — exact match to bronze/staging/fct's known row
count (every Phase 1-3 reconciliation). Independently cross-checked two
ways, not just once:
1. Hand-written SQL: `select count(*) filter (where unique_key is not
   null) from main.fct_service_requests` → `7533132` — exact match.
2. A grouped query (`--group-by metric_time__month`) returned, among
   others: 2026-05 → 331,978; 2026-06 → 334,833; 2024-09 → 306,770 — all
   three **exactly match the ingestion checkpoint's own per-window
   `rows_fetched` values** from Phase 1's `state/backfill_checkpoint.json`
   (an independent historical record, not derived from this query at
   all) — stronger evidence than a single hand-written match alone.

Generated SQL for the grouped query, for the record:
```sql
SELECT
  metric_time__month
  , SUM(__request_count_verification) AS request_count_verification
FROM (
  SELECT
    DATE_TRUNC('month', created_date) AS metric_time__month
    , CASE WHEN unique_key IS NOT NULL THEN 1 ELSE 0 END AS __request_count_verification
  FROM "openledger_prod"."main"."fct_service_requests" sm_c4_1_verification_src_10000
) subq_3
GROUP BY
  metric_time__month
```

### Step 5 — did anything fail requiring the Cube fallback?

**No.** Every step succeeded once the two real (and now documented)
toolchain requirements — a time spine, and matching `DBT_TARGET` to the
manifest's parse target — were satisfied. **No fallback needed. Open-source
MetricFlow via `dbt-metricflow`, against dbt-core + DuckDB, with no dbt
Cloud involvement, is confirmed working end to end**, exactly as
CLAUDE.md's locked decision asserted, with two real operational quirks
now documented (`docs/versions.md`) instead of left to be rediscovered.

**Cleanup**: `_c4_1_verification.yml` deleted; `dim_date`'s time-spine
designation kept (real infrastructure). `dbt build --target prod`
reconfirmed green after removal (94 PASS / 1 WARN / 0 ERROR, 95 nodes —
identical to Phase 3's final state, confirming the verification left no
residue).

### STOP GATE 4, criteria 1-2 — evidence

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | MetricFlow verified on dbt-core + DuckDB | ✅ | Apache-2.0 confirmed via `pip show`; no dbt Cloud (no env vars, traceback confirms local adapter path); trivial semantic model + metric defined and queried end to end; generated SQL captured for both a bare and a grouped query; results cross-checked against hand-written SQL AND an independent historical record (the ingestion checkpoint) |
| 2 | Toolchain path approved | **pending H4.1** | This entry is the verification outcome for H4.1 to review |

**No metric definitions (C4.3) have been written.** Per the user's explicit
instruction, C4.2 (real semantic models) and C4.3 (the metric list) do not
start until H4.1 approves this verification.

---

**H4.1 — APPROVED (2026-08-23)**: MetricFlow on dbt-core + DuckDB, no
Cloud, Apache 2.0 confirmed by inspection.

## C4.2 — Semantic models over the marts

`dbt/models/marts/_semantic_models.yml`. Five semantic models: one over
`fct_service_requests` (grain: one row per `unique_key`, inherited from
the fact's own tested grain, not re-asserted), one each over the four
dimension marts. Entities named identically to the underlying foreign-key
column (`agency_key`, `complaint_type_key`, `location_key`, `date_key`),
so MetricFlow joins them automatically with no `expr` remapping needed.

**One deliberate design rule, to close off an ambiguity risk before it
could matter**: descriptive/categorical attributes (agency name,
complaint_type, borough, calendar fields) are exposed *only* through
their own dimension table's semantic model — never duplicated onto the
fact. `fct_service_requests` already carries a denormalized `borough`
column, but it is *not* declared as a semantic-layer dimension; `borough`
is only reachable via `location_key__borough` through `sm_location`. Two
reachable paths to nominally the same attribute is exactly the kind of
structural ambiguity that could make a filter or join compose differently
depending on which path a query happens to take — closed off at the
design stage rather than found later. The fact's own dimensions are
limited to `created_date` (the time dimension) and attributes that
describe the request itself, not a related entity: `status` and the four
boolean flags (`is_closed`, `is_settled`, `is_censored`,
`is_undated_closure`).

**Two real errors found and fixed getting this to parse/validate, neither
anticipated**:
- YAML `description: Grain: one row per...` — the bare colon after
  "Grain" is parsed as a new mapping key by the YAML loader, not
  literal text. Fixed by quoting the three short one-line descriptions
  that had this shape (the multi-line `>`-block descriptions were
  unaffected).
- `sm_date`'s `year` and `quarter` dimension names collide with
  MetricFlow's own reserved time-granularity keywords
  (`mf validate-configs` rejected both by name, listing the full reserved
  set). Renamed to `calendar_year`/`calendar_quarter` with `expr: year` /
  `expr: quarter` pointing at the real columns.

`mf validate-configs` (run via the sanctioned invocation, `docs/versions.md`):
manifest parse, semantic-model/dimension/entity/measure validation, AND
live validation against the DuckDB warehouse itself — **all six validation
passes green, zero errors, zero warnings** once the two fixes above landed.

## C4.3 — The metrics

`dbt/models/marts/_metrics.yml`. **10 metrics** (8 from phase-4.md's list,
plus `closed_count`/`settled_count` — MetricFlow's `ratio` metric type
references other *metrics* as numerator/denominator, not measures
directly, found from a real parse error: "The metric
`closed_count_measure` does not exist but was referenced by metric
`closure_rate`" — `PydanticMetricTypeParams.numerator`/`.denominator` are
typed `PydanticMetricInput`, a metric pointer, confirmed by reading the
installed package's source. `closed_count`/`settled_count` exist only to
give `closure_rate`/`settlement_rate` something to point at, not as
standalone analytical deliverables of this phase).

**Where every filter actually lives — the design decision C4.4's
invariant proof rests on**: `median_resolution_hours` and
`p90_resolution_hours` carry NO `filter:` at the metric level. The
`is_settled AND is_closed AND NOT is_undated_closure` condition is baked
directly into the underlying measure's own `expr` as a `CASE WHEN`
(`resolution_hours_correct_measure`, `_semantic_models.yml`), fused to
the column reference *inside* the aggregate function's input, not
expressed as a separate clause. This was a deliberate choice, made before
writing any SQL, specifically because a metric-level `filter:` is exactly
the mechanism the user's H4.2 review flagged as the theoretical risk
("a filter applied before vs. after aggregation... a metric that filters
correctly when sliced by agency but leaks when sliced by month"). A
`CASE WHEN` embedded in a measure's `expr` cannot be relocated relative to
the aggregate by MetricFlow's query composition, because it isn't a
separate SQL node at all — it's part of the same expression the aggregate
function receives as input, however that input gets summed, grouped, or
windowed for a given grain.

**A real error caught building `naive_median_resolution_hours`, worth
recording in full**: the first draft defined this measure as `agg:
median, expr: resolution_hours` (the pre-computed column) — reasoning
that an unfiltered read of that column would be "the wrong number."
Checked directly before shipping it (not assumed): **0 rows disagree**
between `resolution_hours IS NOT NULL` and the correct filter
`is_settled AND is_closed AND NOT is_undated_closure`, across the entire
7,533,132-row fact table. `int_request_resolution.sql`'s own censoring
logic already nulls the column out everywhere the "correct" filter would
too — so a measure built on the pre-computed column would always exactly
equal the correct measure, defeating the entire purpose of a trap metric
that's supposed to demonstrate a gap. **Fixed to recompute duration from
the raw columns directly** — `expr: date_diff('hour', created_date,
closed_date)` — bypassing the already-safe `resolution_hours` column
entirely, which is what an analyst unaware `int_request_resolution`'s
censoring logic exists would actually write.

**`complaint_type_share` does not work as defined, reported honestly
rather than silently dropped or misrepresented as working** — full detail
in C4.5 below (the empirical confirmation) and in the metric's own YAML
description. This version of MetricFlow has no "percent of parent total"
metric type (`MetricType` is exactly `{simple, ratio, cumulative, derived,
conversion}`, checked directly against the installed package's source,
not assumed) — a plain `ratio` metric's numerator and denominator both
inherit the *same* query-time group-by, so `request_count /
request_count` grouped by `complaint_type` returns `1.0` for every group,
not a share of the dataset total. Kept defined, not deleted, so this
limitation is demonstrable by running it rather than only described in
prose. Two real options presented to H4.2 rather than a silent
workaround: compute `request_count` grouped by `complaint_type` via
MetricFlow as designed and divide by a separately-queried ungrouped total
in Phase 5's dashboard layer (simple, correct, just not a single
MetricFlow query); or revisit a derived-metric trick later, closer to
Phase 5, rather than force one in now.

## C4.4 — The correctness invariant, proven at the SQL-generation stage

Per the user's explicit instruction: not "queried three cuts and they
looked right" — the generated SQL was read directly, for the same metric,
across genuinely different group-bys, and the filter's position relative
to the aggregation was compared line by line.

**Three shapes tested, all via the sanctioned invocation
(`docs/versions.md`), all with `--explain`:**

1. **Grouped by agency** (a join through `sm_agency`):
```sql
SELECT
  sm_agency_src_10000.agency AS agency_key__agency
  , PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY (subq_1.__median_resolution_hours)) AS median_resolution_hours
FROM (
  SELECT
    agency_key
    , case
      when is_settled and is_closed and not is_undated_closure
      then resolution_hours
      else null
    end AS __median_resolution_hours
  FROM "openledger_prod"."main"."fct_service_requests" sm_service_requests_src_10000
) subq_1
LEFT OUTER JOIN "openledger_prod"."main"."dim_agency" sm_agency_src_10000
  ON subq_1.agency_key = sm_agency_src_10000.agency_key
GROUP BY sm_agency_src_10000.agency
```

2. **Grouped by month** (no dimension-table join, a time-granularity
   truncation instead):
```sql
SELECT
  metric_time__month
  , PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY (__median_resolution_hours)) AS median_resolution_hours
FROM (
  SELECT
    DATE_TRUNC('month', created_date) AS metric_time__month
    , case
      when is_settled and is_closed and not is_undated_closure
      then resolution_hours
      else null
    end AS __median_resolution_hours
  FROM "openledger_prod"."main"."fct_service_requests" sm_service_requests_src_10000
) subq_3
GROUP BY metric_time__month
```

3. **No group-by at all** (a third shape, added beyond what was asked,
   because it removes even the subquery — the simplest possible case):
```sql
SELECT
  PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY (case
    when is_settled and is_closed and not is_undated_closure
    then resolution_hours
    else null
  end)) AS median_resolution_hours
FROM "openledger_prod"."main"."fct_service_requests" sm_service_requests_src_10000
```

**The filter predicate — `case when is_settled and is_closed and not
is_undated_closure then resolution_hours else null end` — is
character-for-character identical in all three, and sits in the identical
structural position in each: inside the row-level `SELECT`/aggregate
input, evaluated per row before any `GROUP BY`, never in a `WHERE` or
`HAVING` clause that could plausibly end up somewhere different depending
on grain.** There is no separate filter node in the query plan for
MetricFlow to relocate — confirming the design choice in C4.3 (filter
fused into the measure's `expr`, not a metric-level `filter:`) is what
makes this invariant actually structural rather than empirically-observed-
so-far. **Criterion 6 satisfied at the SQL-generation level, not just by
querying three cuts and checking the numbers looked right.**

**DHS naive-vs-correct — the real number, not the assumed one.**
phase-4.md's own text predicted "naive vs correct for DHS should differ
by roughly the +17.65pp closure-rate story translated into resolution
hours." Measured directly: **DHS's median_resolution_hours and
naive_median_resolution_hours are both exactly 8.0 hours — identical, no
gap at all.** This is not a bug; it's the correct, precise answer, and
it corrects the phase's own assumption. The reason: DHS's 17,356-row
undated-closure backlog has a null `closed_date`, so
`date_diff('hour', created_date, closed_date)` returns null for those
rows **in the naive computation too** — NULL propagation excludes them
automatically, by accident of arithmetic, not by the intentional
`is_undated_closure` filter. The +17.65pp closure-rate gap (C3.7) is real
and measured, but it is a **closure-rate phenomenon, not a
resolution-hours phenomenon** — the backlog corrupts "what fraction of
DHS's requests count as closed," not "how long did DHS's actually-timed
closures take," because those rows have no timing to corrupt in the first
place. Dataset-wide, a real (smaller) resolution-hours gap does exist —
8.00h correct vs. 7.00h naive, a genuine ~12.5% relative gap, driven by
the closed-but-unsettled/survivorship-bias mechanism, not by DHS at all.
**Both numbers are reported precisely rather than forcing the assumed
DHS-specific translation to appear where measurement shows it doesn't.**

**A related, precise distinction found while reconciling `closure_rate`
by agency (C4.5)**: the semantic layer's `closure_rate` metric (per
phase-4.md's own definition: "closed / total at a given grain") computes
`closed_count / request_count` — i.e. closed as a share of **all**
requests. C3.7's DHS figure (80.97%) is closed as a share of **settled**
requests only, a different, deliberately narrower denominator (excluding
not-yet-old-enough-to-trust cohorts). Both are real, correctly computed
quantities that happen to look similar for DHS specifically (80.97% vs.
81.04% — DHS's settled/total mix is close to 1:1 since the backlog itself
is old) but are not interchangeable in general, and phase-4.md's
`closure_rate` metric is the "of all requests" version, not a
re-implementation of C3.7's own denominator. Flagged for H4.2: if the
semantic layer should also expose the settled-denominator version
specifically, that is a distinct metric to add, not a redefinition of
this one.

## C4.5 — Reconciliation: every metric vs. hand-written SQL

Every metric queried via the sanctioned `mf` invocation
(`docs/versions.md`) and cross-checked against an independent hand-written
DuckDB query, both overall (no group-by) and grouped by agency (16
agencies). **Every value matches exactly — 0 discrepancies.**

| Metric | MetricFlow (overall) | Hand-written (overall) | Match |
|---|---:|---:|---|
| `request_count` | 7,533,132 | 7,533,132 | ✅ |
| `closed_count` | 7,263,932 | 7,263,932 | ✅ |
| `settled_count` | 7,088,809 | 7,088,809 | ✅ |
| `censored_count` | 627,228 | 627,228 | ✅ |
| `closure_rate` | 0.9643 | 0.9643 | ✅ |
| `settlement_rate` | 0.9410 | 0.9410 | ✅ |
| `median_resolution_hours` | 8.00 | 8.0 | ✅ |
| `p90_resolution_hours` | 401.00 | 401.0 | ✅ |
| `naive_median_resolution_hours` | 7.00 | 7.0 | ✅ |

**By agency (16 rows each, `median_resolution_hours` /
`naive_median_resolution_hours` / `closure_rate`)**: every one of the 16
agencies' three values matched exactly between `mf query --group-by
agency_key__agency` and the equivalent hand-written `GROUP BY a.agency`
query — including the DHS row (8.0 / 8.0 / 0.8104) and the widest-gap
agency, DOB (223.0 / 158.0 — a real ~29% naive-vs-correct gap, the largest
of any agency, not investigated further here but worth flagging for
Phase 5 as a candidate for a per-agency naive-vs-correct panel).

**`complaint_type_share` was queried and confirmed to return exactly
1.0000 for every complaint type** (VENDOR ENFORCEMENT, WATER LEAK,
RESIDENTIAL DISPOSAL COMPLAINT, ABANDONED BIKE, BOILERS all checked) —
the predicted failure mode from C4.3, demonstrated empirically rather
than left as a theoretical concern. Not reconciled against hand-written
SQL because it isn't computing the intended quantity at all; there is
nothing correct to reconcile against yet.

**No discrepancy required resolution** — every metric that is supposed to
compute what its name says computed exactly the hand-written answer, on
the first working definition, across every grain tested.

### STOP GATE 4 — updated status

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | MetricFlow verified on dbt-core + DuckDB | ✅ | C4.1 |
| 2 | Toolchain path approved | ✅ | H4.1, 2026-08-23 |
| 3 | Semantic models bind to marts | ✅ | 5 semantic models (fact + 4 dims), entities/dimensions/measures declared, `mf validate-configs` fully green including live warehouse validation |
| 4 | Metrics defined | ✅ | 10 metrics (8 from phase-4.md + 2 ratio-support metrics); resolution metrics carry the settlement/backlog filter inside the measure's own expr |
| 5 | Metric definitions approved | **pending H4.2** | This entry is the outcome for H4.2 to review |
| 6 (load-bearing) | Correctness invariant proven | ✅ | Generated SQL read directly for 3 group-by shapes (agency/month/none); identical filter, identical position (row-level, pre-aggregation) in every case — not just 3 cuts that "looked right" |
| 7 | Naive-vs-correct gap quantified | ✅ | Dataset-wide: 8.00h vs 7.00h (+12.5% relative). DHS specifically: 8.0h vs 8.0h, NO gap — phase-4.md's assumption corrected with the measured reason why |
| 8 (load-bearing) | Every metric reconciles to hand-written SQL | ✅ | 9 metrics, overall + by-agency (16 agencies), 0 discrepancies. `complaint_type_share` confirmed broken as designed, not reconciled (nothing correct to reconcile against) |
| 9 | Metrics documented as an interface | not yet (C4.6, after H4.2) | |
| 10 | Journal, metrics, findings updated | partial | This entry; `docs/metrics.md`/`docs/findings.md` updates follow C4.6/C4.7, after H4.2 |
| 11 | One atomic commit | not yet (C4.7, after H4.2) | |

**The DBT_TARGET/Phase 6 consistency check the user asked for**: confirmed
there was exactly one documented invocation form as of this session — no
divergent variant existed anywhere to reconcile — but the earlier C4.1
writeup in `docs/versions.md` DID present a second, cwd-default-discovery
form as "also works," which is exactly the kind of ambiguity that could
let a future Phase 6 job diverge. **Fixed**: `docs/versions.md` now states
one sanctioned invocation only, explicitly marked as the exact form
Phase 6's scheduled job must reuse verbatim, with the alternative
form's existence noted only to say it is deliberately not used. Every
`mf` call in this session's C4.2-C4.5 work (semantic model validation,
the invariant proof, the full reconciliation) used this exact form,
consistently — dogfooded, not just documented.

---

**H4.2 — APPROVED (2026-08-23)** for the 9 working metrics, with one
redesign (the naive-vs-correct trap pairing) and one decision
(`complaint_type_share` removed, not shipped broken).

## C4.3 redesign — two traps, two metric pairs, each verified to differ

**The problem, stated by the review**: `naive_median_resolution_hours`'s
own measured result (DHS: 8.0h naive vs. 8.0h correct — no gap) meant the
resolution-hours trap pair could never carry the DHS story, because
undated-closure rows null out of *any* resolution-hours computation via
ordinary NULL arithmetic (`date_diff` against a null `closed_date`),
whether or not a filter intentionally excludes them. The project's
strongest correctness finding (C3.7's +17.65pp closure-rate delta) would
have had no metric-pair demonstration at all.

**The fix**: moved the backlog trap to a metric where it's actually
visible — closure rate, not resolution hours. Two new metrics,
`naive_closure_rate` and `closure_rate_excl_backlog`
(`dbt/models/marts/_metrics.yml`), both denominated over the **settled**
population (matching C3.7's own methodology exactly, not the "of all
requests" denominator `closure_rate` already uses), differing only in
whether the undated-closure backlog is excluded from that denominator.
`naive_median_resolution_hours` is kept, relabeled: it demonstrates the
settlement/censoring trap, explicitly *not* the backlog trap, in both its
own YAML description and `docs/metrics_interface.md`.

**A second real numerator bug, caught by doing exactly what the review
asked ("verify it actually differs... so it isn't a second silent
no-op") — found, not just avoided**: the first working draft of
`naive_closure_rate` used `closed_count` (unconditional — count of all
`is_closed` rows, with no settlement requirement) as the numerator over
the settled-only denominator. Queried it before writing anything else
down: **dataset-wide rate = 102.47%, and several individual agencies
showed rates above 1.0** — an impossible value for a ratio of counts,
which would have shipped silently as a plausible-looking 4-decimal number
if not checked against a hand-written query first. Root cause: `is_closed`
and `is_settled` are independent conditions — a row can be closed but not
yet settled (closed within the last `observation_cutoff_days`) — so
`closed_count` is **not a subset of** `settled_count`, and dividing the
two can legitimately exceed 1. Fixed with a new, correctly-nested measure,
`closed_and_settled_count_measure` (`is_closed AND is_settled`), used as
the numerator for both `naive_closure_rate` and `closure_rate_excl_backlog`
— `is_closed`/`is_undated_closure` remain mutually exclusive by
construction (a `closed_date` can't be both null and not-null), so this
same numerator is correct for both metrics; only their denominators
differ.

**Verified after the fix, both dataset-wide and per-agency (all 16),
reconciled against hand-written SQL — 0 discrepancies**:

| Grain | `naive_closure_rate` | `closure_rate_excl_backlog` | Gap |
|---|---:|---:|---:|
| Dataset-wide | 97.42% | 97.66% | +0.24pp |
| **DHS** | **80.97%** | **98.60%** | **+17.63pp** |
| DSNY (the only other agency with any backlog rows) | 99.06% | 99.07% | +0.01pp |
| All other 14 agencies | identical between the two (no backlog rows) | — | 0.00pp |

DHS's figures (80.97% / 98.60%) match C3.7's independently-published
80.97%/98.61% almost exactly (0.01pp rounding difference) — the semantic
layer reproduces the hand-derived finding from a completely different
code path, which is itself a form of reconciliation. **The pair now
genuinely demonstrates the backlog trap, verified rather than assumed,
exactly as the review required.**

## C4.3 decision — `complaint_type_share` removed, not shipped broken

Per H4.2: deleted from `_metrics.yml` entirely (previously kept-but-
flagged; the review correctly rejected "broken but labeled" as
insufficient). The MetricFlow limitation (no "percent of parent total"
metric type in this version, confirmed by inspecting the installed
package's `MetricType` enum, and empirically — a bare ratio of
`request_count` over itself, grouped by `complaint_type`, returns exactly
`1.0` for every group) is recorded here as a real, found constraint, not
worked around. **Chosen path**: Phase 5 computes the share in its own
query/dashboard layer — `request_count` queried once grouped by
`complaint_type` and once ungrouped for the dataset total, divided
client-side — rather than waiting on a future MetricFlow release. This
needs no new code today; it's a two-query pattern against a metric that
already exists (`request_count`), documented in
`docs/metrics_interface.md` as the intended Phase 5 usage.

## C4.6 — Metrics documented as an interface

`docs/metrics_interface.md`: every one of the 9 real metrics (plus the 4
internal ratio-plumbing metrics, explicitly marked as not part of the
interface) — what it means, what it enforces, measured values, and the
one sanctioned query invocation, all readable without opening either
YAML file. Includes the naive-vs-correct gap for both trap pairs and the
verification note about the numerator bug (kept visible, not scrubbed
out, per this project's standing practice of recording a caught bug
rather than only its fix).

## A second occurrence of the partial-parse staleness trap (Phase 3's finding, recurring)

Removing `complaint_type_share` from `_metrics.yml` and rebuilding did
**not** remove it from `mf list metrics` — it kept reporting 14 metrics,
`complaint_type_share` included, until `target/partial_parse.msgpack` was
deleted and the project rebuilt fresh, after which it correctly reported
13. Identical mechanism to the unit-test date staleness found in Phase 3
(`docs/decisions.md`, C3.3 addendum): dbt's partial-parse optimization
reused a stale prior parse of `_metrics.yml` instead of detecting the
metric's removal, even though the file content had changed (a metric
*removed*, not just a `datetime.now()` re-render, so this is a second,
independently-triggered instance of the same class of staleness, not a
repeat of the exact same bug). **Reinforces the Phase 6 conclusion already
on record**: a scheduled job reusing a persistent `target/` directory
across runs needs either `--no-partial-parse` or a clean `target/` each
run, or changes to semantic-layer definitions (not just data) could
silently fail to take effect.

## STOP GATE 4 — final status

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | MetricFlow verified on dbt-core + DuckDB | ✅ | C4.1 |
| 2 | Toolchain path approved | ✅ | H4.1, 2026-08-23 |
| 3 | Semantic models bind to marts | ✅ | 5 semantic models (fact + 4 dims); `mf validate-configs` fully green including live warehouse validation |
| 4 | Metrics defined | ✅ | 9 real metrics + 4 ratio-plumbing metrics (13 total registered); resolution and closure-rate metrics carry their filters inside the underlying measure's own expr |
| 5 | Metric definitions approved | ✅ | H4.2, 2026-08-23 — 9 metrics approved, 1 redesigned into 2, 1 removed |
| 6 (load-bearing) | Correctness invariant proven | ✅ | Generated SQL read for 3 group-by shapes (agency/month/none) on `median_resolution_hours`; identical filter, identical position in every case |
| 7 | Naive-vs-correct gap quantified | ✅ | Two pairs, two traps: resolution-hours (8.00h vs 7.00h, settlement trap) and closure-rate (98.60% vs 80.97% for DHS, backlog trap) — each verified to actually differ, not assumed |
| 8 (load-bearing) | Every metric reconciles to hand-written SQL | ✅ | All 9 real metrics, overall + by-agency (16 agencies), 0 discrepancies after fixing 2 real bugs found in the process (the naive-measure no-op, the unnested-numerator >100% rates) |
| 9 | Metrics documented as an interface | ✅ | `docs/metrics_interface.md` |
| 10 | Journal, metrics, findings updated | ✅ | This entry; `docs/metrics.md`, `docs/findings.md` (C4.7) |
| 11 | One atomic commit | pending | C4.7, this session |

**Load-bearing criteria 1, 6, 8 all hold with real depth**: 1 required
finding and fixing two undocumented toolchain requirements (time spine,
target-matching) before anything else could work; 6 was proven at the
SQL-generation level across 3 shapes, not just by querying and eyeballing
results; 8 caught two real, would-have-shipped-silently bugs (a trap
metric that was secretly a no-op, and a ratio numerator that produced
impossible >100% rates) specifically *because* reconciliation was treated
as a verification step, not a formality to wave through.

---

# Phase 5 — Evidence.dev Dashboard, Deployed Public

## C5.1 — Build-time data delivery (resolved before any page was written)

**Chosen: option 1, a small committed DuckDB file** — `dashboard/sources/openledger/openledger.duckdb`,
built by `scripts/build_dashboard_source.py`, reading the full 596MB prod
warehouse (read-only) and writing ONLY pre-aggregated tables — never raw
fact rows. **Size: 2.26 MB** — trivially committable, no fetch-at-build-time
mechanism needed.

Ten tables, one per dashboard need, each computed with the EXACT SAME
filter logic as the corresponding Phase 4 MetricFlow metric (not a
re-derivation from scratch): `overview` (1 row), `agency_performance`
(16 rows), `geo_equity` (57 rows, top-10 complaint types × borough),
`geo_equity_missing_coords` (1 row), `seasonality` (230 rows, top-10
types × complete months only — see the seasonality finding below),
`dq_scorecard` (103 rows, a direct pull of the live Phase 3 scorecard),
`dq_settlement_completeness` (4), `dq_vocabulary_drift` (398),
`dq_mass_touch` (6, fired nights only), `dq_resolution_hours_naive_vs_correct` (1).

**Connection confirmed working locally**: `evidence sources` (the classic
Evidence CLI, via the `@evidence-dev/duckdb` connector,
`dbt/sources/openledger/connection.yaml` → `filename: openledger.duckdb`)
materializes all 10 as Parquet in one run, every table's row count
matching what was written.

**Reconciled against the full warehouse and MetricFlow independently,
not just internally consistent with itself**: `overview`'s
`request_count` (7,533,132), `median_resolution_hours` (8.0),
`p90_resolution_hours` (401.0), `closure_rate_pct` (96.43%), and
`settlement_rate_pct` (94.10%) all match `mf query`'s live output exactly.
`agency_performance`'s DHS row (`naive_closure_rate_pct` 80.97%,
`closure_rate_excl_backlog_pct` 98.60%) matches both `mf query` and
C3.7's original hand-derived figures. Full detail in C5.6 below.

## A real toolchain trap: the "official" Evidence template now scaffolds a different, incompatible product

Before writing `scripts/build_dashboard_source.py`, tried the documented
scaffolding path first: `npx degit evidence-dev/template`. **This is no
longer the classic, self-hosted, static-build Evidence CLAUDE.md's plan
requires** — the repo's own README now describes "Evidence Studio," a
separate hosted product requiring `evidence login` (browser-based auth)
and defaulting to a remote "Evidence Warehouse" backend unless a
`connection.yaml` opts out — the opposite of the free, always-on,
no-server static site this phase needs. **Verified this is a real product
split, not a misreading**: the classic open-source package,
`@evidence-dev/evidence` (npm, MIT, version 40.1.8, last published
2026-02-06 — actively maintained, not abandoned), still exists
separately, uses `@sveltejs/adapter-static` (confirmed via its own
`package.json` dependencies), and exposes exactly the CLI this phase
needs: `evidence dev`, `evidence build`, `evidence sources`, `evidence
preview` — no login, no hosted warehouse. Scaffolded by hand instead of
via `degit` (`dashboard/package.json` + `dashboard/evidence.config.yaml`
written directly), since the templated starter no longer matches what
this project needs.

**Five real, undocumented dependency/environment issues found and fixed
getting a first build to succeed, in the order hit** (kept in full,
per this project's standing practice — this class of "the scaffold works
until you actually run it" discovery is exactly what a future session or
Phase 6's CI needs on record):
1. `package.json` needs `"type": "module"` — omitted by default, and its
   absence makes Node treat Evidence's ESM-only internals as CommonJS,
   failing with "resolved to an ESM file" errors at every internal import.
2. `@evidence-dev/tailwind` is a required peer, not bundled — build fails
   with `Cannot find package '@evidence-dev/tailwind'` otherwise.
3. Installing `@sveltejs/vite-plugin-svelte` without an explicit version
   pin resolves to its LATEST major (7.x, which requires Vite 8) while
   `@evidence-dev/evidence` itself is built against Vite 5.4 and
   `@sveltejs/vite-plugin-svelte@3.1.2` specifically. The mismatch doesn't
   error at install time (`--legacy-peer-deps` suppresses the conflict
   warning) — it fails much later, deep in Vite's plugin system
   (`Cannot read properties of undefined (reading 'config')`, because
   `this.environment` — a Vite 6+ Rollup API — doesn't exist on Vite 5).
   Fixed by pinning the exact compatible version, not just "a" version.
4. `autoprefixer`/`postcss` are needed directly (not just as `tailwindcss`
   transitive deps) for the project's PostCSS config to load.
5. `git-remote-origin-url` is needed by an internal Evidence API route
   (`api/settings.json`) that ships in every build regardless of whether
   it's used.

**Final, verified-working dependency set**: `dashboard/package.json`
(`@evidence-dev/evidence@40.1.8`, `@evidence-dev/duckdb@^2.0.1`,
`@evidence-dev/core-components@^5.4.2`, `@evidence-dev/tailwind@^3.1.4`,
`@sveltejs/vite-plugin-svelte@3.1.2` exact, `git-remote-origin-url@^4.0.0`,
`autoprefixer`/`postcss` as devDependencies) plus `dashboard/.npmrc`
(`legacy-peer-deps=true`, so Vercel's own `npm install` — which would
otherwise hit issue 3's ERESOLVE conflict — succeeds without a manual
flag). **Verified from a fully clean install** (`rm -rf node_modules
package-lock.json && npm install`, no flags) that this reproduces
correctly — the exact scenario Vercel's build environment will run.

## C5.2 — Evidence scaffold

`dashboard/` — `evidence.config.yaml` (declares the `@evidence-dev/duckdb`
datasource plugin and `@evidence-dev/core-components`),
`sources/openledger/` (`connection.yaml` + one `.sql` file per pruned
table, each a trivial `select * from <table>` — Evidence's convention:
one source-directory `.sql` file per exposed dataset, named
`<source>.<file_stem>` in pages), `pages/` (the 5 pages, C5.3).
`npm run dev` and `npm run sources`/`npm run build` all confirmed working
locally against the pruned source.

## C5.3/C5.4/C5.5 — The five pages

`pages/index.md` (Overview), `agency-performance.md`,
`geographic-equity.md`, `seasonality.md`, `data-quality.md`. Naive-vs-correct
shown prominently, not buried: the DHS backlog pair
(`naive_closure_rate_pct` 80.97% vs. `closure_rate_excl_backlog_pct`
98.60%) on Agency Performance, and the settlement/censoring pair (7 vs. 8
median hours) on Data Quality — matching Phase 4's own relabeling of
which trap each pair demonstrates. Honesty annotations: the ~1.72%
missing-coordinate exclusion stated on Geographic Equity, provisional-
period framing built into every resolution metric's own definition
(settled-only, inherited from Phase 4, not re-derived per page).

**A real honesty-annotation finding, caught while reconciling the
seasonality page (C5.6), not anticipated going in**: the naive
month-over-month volume range across the FULL raw data is 119,270 (Aug
2024) to 348,511 (Jan 2026) — a range that looks far less flat than the
page's own "volume barely moves" claim. Investigated rather than
softened: Aug 2024 is the backfill's own partial start month (data begins
2024-08-19, not the 1st) and the dataset's current last month is a
partial, stale-bronze end (bronze's last ingested row is 2026-08-20).
Excluding just those two calendar months, the range is 255,364-348,511 —
genuinely flat-ish, matching the claim. **Fixed at the source**:
`scripts/build_dashboard_source.py`'s `seasonality` query now excludes
the calendar month containing the dataset's min and max `created_date`
from every chart on that page, with an `<Alert>` on the page itself
stating the exclusion and why — plotting the two partial months
unfiltered would have visually manufactured a "volume crashes at both
ends" story the underlying data doesn't support.

## C5.6 — Reconciliation

Every page's headline figure checked against a hand-written query on the
**full** 7,533,132-row prod warehouse (not the pruned source, not
MetricFlow — a third, fully independent path):

| Page | Headline figure | Pruned/dashboard value | Full-warehouse hand-written value | Match |
|---|---|---:|---:|---|
| Overview | `request_count` | 7,533,132 | 7,533,132 | ✅ |
| Overview | `median_resolution_hours` | 8.0 | 8.0 | ✅ |
| Overview | `closure_rate_pct` | 96.4265% | 96.43% (rounded) | ✅ |
| Agency Performance | DHS `naive_closure_rate_pct` | 80.973% | 80.97% (`mf query`, matches C3.7) | ✅ |
| Agency Performance | DHS `closure_rate_excl_backlog_pct` | 98.6002% | 98.60% (`mf query`, matches C3.7) | ✅ |
| Geographic Equity | missing-coordinate rate | (page states ~1.72%) | 1.72% | ✅ |
| Seasonality | HEAT/HOT WATER peak monthly share | (page states "over 20%") | 22.93% | ✅ |
| Data Quality | naive/correct median resolution hours | 7 / 8 | 7.0 / 8.0 (`mf query`, matches C4.4) | ✅ |

**0 discrepancies.** The seasonality finding above was caught specifically
*while doing this reconciliation* (the raw min/max didn't match the
page's own flat-volume claim until the two partial months were
identified) — direct evidence this step is a real check, not a formality.

## C5.7 — Build and deploy (prepared; H5.1 is the user's account-level step)

`dashboard/vercel.json`: `buildCommand: "npm run sources && npm run build"`
(regenerates Parquet from the committed pruned DuckDB, then the static
site — both from source, nothing pre-built is committed),
`outputDirectory: "build"`, `framework: null` (disables Vercel's
SvelteKit auto-detection, which would otherwise assume the serverless
Vercel adapter rather than the static one this project actually uses).
**Verified this exact command sequence locally, from a clean install,
before handing off** — the same thing Vercel's build environment will run.

**For H5.1**: connect the GitHub repo, set the Vercel project's **Root
Directory to `dashboard`** (the repo is a monorepo; `vercel.json` and
`package.json` both live there, not at the repo root), deploy, then
confirm the public URL loads anonymously in a private browser window.

## C5.8 — The refresh story

**Full path**: (1) `python3 scripts/build_dashboard_source.py` — rebuilds
`openledger.duckdb` from the current prod marts; (2) commit the
regenerated file (it's small, ~2MB, a real diff each time); (3) push —
Vercel's git integration rebuilds automatically on push to the connected
branch, running `vercel.json`'s `buildCommand` (which itself re-runs
`evidence sources` against the newly-committed DuckDB file, so the
Parquet the site actually serves is always regenerated from the latest
committed data, never stale build output). Step (3) can alternatively be
a Vercel **deploy hook** (a webhook URL, created in the Vercel project's
settings) fired by Phase 6's cron after step (2)'s commit, without
needing a new git push per se — full wiring is Phase 6's job.

**Proven in full, this session, end to end** (2026-08-24, after H5.1
fixed the Vercel project's Root Directory to `dashboard` and confirmed
the live deploy): steps (1)-(3) run for real, not merely described.
`python3 scripts/build_dashboard_source.py` re-read the (unchanged since
Phase 4 — no new ingestion ran this session) prod warehouse and rewrote
`openledger.duckdb`; the logical content was verified identical
(`request_count`/`closure_rate_pct`/`median_resolution_hours` all
matched the previous build exactly) while the **file bytes still
differed** (`git diff --stat` showed a real change despite 0 rows
changing) — a real, worth-recording observation: DuckDB's on-disk layout
isn't byte-deterministic across writes even for identical logical
content (likely internal metadata/ordering), so "no git diff" is never a
valid signal that the underlying data didn't change, and conversely "a
diff exists" doesn't by itself mean the data moved. This exact rebuild
was committed and pushed to `main` in the same commit as the README URL
addition — that push is what fired Vercel's git-integration redeploy
(no separate deploy-hook call needed for a plain content refresh, since
the project is already connected to this repo/branch). **Confirmed via
an independent `WebFetch` of the live URL after the push**, not assumed
from the push succeeding: the site re-rendered with the same reconciled
figures (7,533,132 requests, 96.4% closure rate, 8h median — unchanged,
as expected since the underlying data didn't move), all 5 navigation
links present.

**The manual steps Phase 6 must automate, stated precisely**:
1. `python3 scripts/build_dashboard_source.py` (from repo root, main venv
   active) — rewrites `dashboard/sources/openledger/openledger.duckdb`
   from whatever the prod warehouse currently contains. Depends on
   `dbt/target/openledger_prod.duckdb` being current — this step must run
   **after** that phase's `dbt build --target prod`, never before.
2. `git add dashboard/sources/openledger/openledger.duckdb && git commit
   -m "..." && git push origin main` — the push is the trigger; no
   Vercel CLI or API call needed as long as the project's GitHub
   integration stays connected to this branch.
3. Verification (what a human or Phase 6's job did/should do): confirm
   the new deployment shows the expected data, not just that the build
   succeeded — a green Vercel build with silently-stale or broken data
   underneath it is a worse failure mode than an obviously-failed build,
   because nothing flags it. `WebFetch`-equivalent, or a lightweight
   scripted check against the live URL's rendered totals, is the cheap
   version of this for a cron job.

## STOP GATE 5 — CLOSED (2026-08-24)

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 (load-bearing) | Build-time data path works | ✅ | 2.26MB pruned DuckDB, `evidence sources` confirmed materializing all 10 tables |
| 2 | Pruned data reconciles to marts | ✅ | C5.1/C5.6, 0 discrepancies |
| 3 | All five pages render locally | ✅ | `evidence build` clean, all 5 pages verified for real rendered content |
| 4 | Naive-vs-correct shown | ✅ | DHS backlog pair (Agency Performance), settlement pair (Data Quality) |
| 5 | Provisional metrics annotated | ✅ | Missing-coordinate note, seasonality partial-month exclusion + alert, settled-only filters inherited from Phase 4 |
| 6 (load-bearing) | Headline numbers reconcile | ✅ | C5.6 table, 0 discrepancies, caught one real finding (seasonality edge months) in the process |
| 7 (load-bearing) | Deployed and public | ✅ | https://openledger-three.vercel.app — H5.1 done (Root Directory fixed after the initial 404), confirmed loading anonymously |
| 8 | All pages render live | ✅ | H5.2: all five pages confirmed by the user; independently re-verified via `WebFetch` post-refresh (this entry) |
| 9 | Ninety-second test passed | ✅ | H5.2 passed |
| 10 | Refresh path documented and proven once | ✅ | Full loop run for real this session: rebuild → commit → push → live redeploy → confirmed via independent fetch |
| 11 | Journal, metrics updated; URL in README stub | ✅ | This entry, `docs/metrics.md`; README now has the live URL |
| 12 | One atomic commit | ✅ | C5.9 initial commit `d014afc`; this URL/refresh addition is a small follow-up, matching the two-commit pattern already used in Phase 3 |

**Load-bearing criteria 1, 6, 7 all hold with real depth**: 1 and 6 from
C5.1/C5.6's original reconciliation work; 7 survived a real failure
first (the initial deploy 404'd because nothing had been pushed and the
Root Directory wasn't set) and is now independently re-confirmed live,
not just trusted from the user's report.
