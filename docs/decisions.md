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
