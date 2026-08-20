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

### C1.5 — partition-scoped upsert, measured against the real full-scale table

- **Measured:** with bronze at its final state (7,522,072 rows, 170 snapshots, 869
  Parquet files across ~730 day-partitions), a real 500-row no-op batch (all
  `created_date=2025-03-15`, already correctly present) was upserted twice: once
  via PyIceberg's plain `table.upsert()` (unscoped — its match-scan is built purely
  from a `unique_key IN (...)` predicate, with no partition awareness) and once via
  `bronze.scoped_upsert()` (ANDs a `created_date` partition-range predicate into
  the same scan).
- **Result: unscoped 3.37s vs. scoped 0.09s — a 38.8x speedup**, on the real,
  fully-populated table (not a small synthetic one — the effect is invisible on a
  table with only a handful of partitions, which is why this was deferred until
  bronze reached full scale rather than measured on the earlier smoke tests).
- **Why:** the unscoped scan must consider manifests across all ~730 partitions to
  find any file whose `unique_key` stats might match; the scoped scan prunes to the
  1 partition (`2025-03-15`) the batch actually touches before ever evaluating
  `unique_key`. This is the "real optimization with a number attached" phase-1.md
  asked C1.5 to produce.
- **Caveat:** this benefit is largest for backfill-style batches (chunked by
  `created_date`, so `min(days)..max(days)` is tight — often a single day). It's
  much weaker for `:updated_at`-chunked incremental batches, whose touched
  `created_date` values can scatter across the entire 24-month range (a batch
  spanning many partitions widens `scoped_upsert`'s partition-range predicate
  toward the full table) — not measured separately here, but worth knowing before
  assuming the 38.8x figure holds for every call site.
