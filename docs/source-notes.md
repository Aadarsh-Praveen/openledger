# Source Notes

## H0.2 — Dataset identity and current shape (human-verified, verbatim)

Recorded by the project owner from the live NYC Open Data / Socrata dataset landing
page. Not inferred or supplemented from model knowledge.

- **Title:** 311 Service Requests from 2020 to Present
- **Update cadence:** Daily
- **Row count:** 22.2M
- **Column count:** 44
- **Date range:** 2020-01-01 to present
- **Historical 2010–2019 dataset:** exists separately, ID `76ig-c548`
- **API endpoint:** `https://data.cityofnewyork.us/resource/erm2-nwe9.json`

## C0.3 — Source verification findings

Run: `.venv/bin/python scripts/verify_source.py`, executed live against
`https://data.cityofnewyork.us/resource/erm2-nwe9.json` on 2026-08-19.

| # | Question | Observed value | Implication for Phase 1 |
|---|---|---|---|
| 1 | Reachability — HTTP 200, token sent, schema | HTTP 200. `X-App-Token` header confirmed present on the outbound request. Single-row response returned **32** present JSON keys (not all 44 dataset-page columns — Socrata's JSON output omits fields that are null for that row, so a single sample row will not show every possible column). | Column presence must be treated as per-row, not fixed — the ingest schema must tolerate a row lacking any given optional field, not assume all 44 always appear. |
| 2 | `$where` on `created_date` | Requested window 2026-08-01T00:00:00.000–2026-08-07T23:59:59.999, `$order=created_date`, `$limit=5000`. 5,000 rows returned (limit-bound, so response only spans the first part of the week by volume). Min returned `created_date`: 2026-08-01T00:00:14.000. Max: 2026-08-01T15:01:48.000. **0 rows fell outside the requested window.** | `$where` filtering on `created_date` is reliable — the watermark-based incremental design holds. |
| 3 | Real page-size cap | Requested `$limit=50000` → **50,000 rows returned** (the full request, not clamped). | Cap is at least 50,000/page, far above the plan's assumed 1,000–5,000. Phase 1 pagination should use a large page size, cutting the number of requests needed dramatically. |
| 4 | `$offset` pagination stability | `$order=created_date,unique_key`, page size 5,000. Page 1 (offset 0): 5,000 keys. Page 2 (offset 5,000): 5,000 keys. **Overlap in `unique_key` between pages: 0.** Page 1 re-pulled: **identical** to the first pull. | Pagination with a deterministic total order (`created_date,unique_key`) is stable and non-overlapping — safe for Phase 1's paginated ingest loop. |
| 5 | Backfill row count (24-month window) | `$select=count(*)`, `$where=created_date >= '2024-08-19T00:00:00.000'` (24 months before 2026-08-19, the run date). **Count: 7,511,072 rows.** | This is the number Phase 1's ingestion must reconcile against for the chosen 24-month backfill. Higher than H0.3's rough estimate of ~6.7M (based on a naive full-history average), still comfortably tractable. |
| 6 | Throughput / throttling | 20 sequential paginated requests (5,000 rows/page each). Median latency: **0.426s**. Total elapsed: **8.496s**. Non-200 responses: **none**. Rate-limit headers observed: **none**. Derived: at the observed 50,000-row page cap, the 7,511,072-row backfill needs 151 pages; at median latency that's an estimated **0.02 hours (~65 seconds)** of pure request time — far under the 2-hour flag threshold. | No throttling encountered in this short probe. The app-token throttle is reportedly ~1,000 requests/hour (per H0.3 rationale); 151 requests for the full backfill is well within that budget. Real-world backfill time will be dominated by write/MERGE cost in Phase 1, not by API latency. |

**H0.3 decision-rule outcome:** measured page-size cap (50,000) ≥ 10,000 → **rule says
keep the 24-month backfill window.** Recorded in `docs/decisions.md`.

## C0.4 — Data-quality baseline findings

Run: `.venv/bin/python scripts/probe_data_quality.py`, executed live against the same
endpoint on 2026-08-19. Sample window: **2026-07-01T00:00:00.000 to
2026-07-31T23:59:59.999** (one full calendar month). Sample size: **342,892 rows**
(all rows in the window were pulled, not a sub-sample).

| Finding | Count | Rate | Note |
|---|---|---|---|
| `closed_date` < `created_date` | 65 | 0.0190% | Present but rare in current data. |
| status = "Closed" with null `closed_date` | 1 | 0.0003% | Essentially does not exist in this window. |
| Missing `latitude`/`longitude` | 6,771 | 1.9747% | The most material defect measured. |
| Coordinates at (0,0) or outside NYC bounds | 0 | 0.0000% | **Does not occur** in this sample — 0 of 342,892 rows. |
| Distinct `complaint_type` values | 176 | — | — |
| Distinct `agency` values | 14 | — | — |
| `borough` "Unspecified" or null | 460 | 0.1342% | Present but small. |

These are the real, measured rates for July 2026 — several of the classically-cited
311 data-integrity defects (closed-before-created, closed-with-no-close-date,
out-of-bounds coordinates) are rare-to-nonexistent in current data. This is reported
plainly per CLAUDE.md's standing rule on documented negative findings; Phase 3's DQ
scorecard should be built around what's actually present (missing coordinates and
unspecified borough are the two with a measurable rate) rather than the historically
assumed defect mix.

## C0.5 — Iceberg local stack: on-disk warehouse layout

Run: `.venv/bin/python scripts/verify_iceberg_stack.py` — smoke test on a namespace
`smoke`, table `phase0_test`, format version 2, partitioned by `day(event_ts)`.
All assertions passed: format v2 confirmed; snapshot count went 0 → 1 → 2 across two
appends; `upsert()` on a colliding key left row count unchanged (5 → 5) and updated
the value; a time-travel read against the first snapshot correctly returned the
pre-second-batch state (3 rows, ids `[1, 2, 3]`); DuckDB's `iceberg_scan` against the
table root read back the same row count (5) as PyIceberg. Test table and namespace
were dropped and purged afterward — this tree no longer exists on disk.

Directory layout observed immediately before cleanup:

```
warehouse/
├── .gitkeep
└── smoke/
    └── phase0_test/
        ├── data/
        │   ├── event_ts_day=2026-08-01/
        │   │   └── <3 data parquet files>
        │   └── event_ts_day=2026-08-02/
        │       └── <1 data parquet file>
        └── metadata/
            ├── 00000-<uuid>.metadata.json   # after create_table
            ├── 00001-<uuid>.metadata.json   # after append 1
            ├── 00002-<uuid>.metadata.json   # after append 2
            ├── 00003-<uuid>.metadata.json   # after upsert
            ├── <uuid>-m0.avro / -m1.avro    # manifest files
            └── snap-<snapshot_id>-0-<uuid>.avro  # manifest lists, one per snapshot
```

Two points worth carrying into Phase 7 (which mirrors this layout to S3):
- Data files are Hive-style partitioned by the transform-derived directory name
  (`event_ts_day=<date>`), one subdirectory per partition value, under `data/`.
- Each write operation (append, upsert) produces a new `metadata.json` snapshot file
  plus new manifest (`-m*.avro`) and manifest-list (`snap-*.avro`) files — old
  metadata/manifest files are retained (not deleted) unless explicitly expired, which
  is why an upsert on one row produced 2 extra data files in that partition (a new
  data file plus the delete tracked via manifest, per Iceberg's copy-on-write/
  merge-on-read append+overwrite mechanics for `upsert()`).

## Known caveat for DuckDB Iceberg reads (Step 7 detail)

DuckDB's `iceberg_scan` in this installed extension build resolves its path argument
as the **table root** (it looks for `<path>/metadata/...` itself) rather than
accepting a `metadata.json` file path directly — passing the metadata file path
produces a `<file>.metadata.json/metadata/...` path-join error. Additionally, because
PyIceberg's `SqlCatalog` does not write a `version-hint.text` file, DuckDB requires
`SET unsafe_enable_version_guessing = true;` to glob the metadata directory for the
latest snapshot. Recorded in `docs/decisions.md` as a platform quirk.

## C1.1 — Watermark field verification findings

Run: `.venv/bin/python scripts/probe_watermark.py`, executed live against
`https://data.cityofnewyork.us/resource/erm2-nwe9.json` on 2026-08-19. This exists
to test — not assume — the Phase 1 design problem: a `created_date` watermark
produces permanently stale rows, and the plan's candidate fix is `:updated_at`.

| # | Question | Observed value | Implication |
|---|---|---|---|
| 1 | Does `:updated_at` exist and is it selectable? | Yes. `$select=unique_key,created_date,:updated_at` returned the field on every sampled row (5/5). Sample values are ISO-8601 with a trailing `Z` (offset-aware), unlike `created_date`, which has no timezone suffix. | Selectable. Ingestion code must normalize the two timestamp formats before comparing them. |
| 2 | Is `:updated_at` filterable in `$where`? | Yes, verified by content not just status code: bounded window request (2026-08-01T00:00–2026-08-07T23:59) returned 5,000 rows, **0 outside the requested bound** (min = max = the actual returned value, both inside). | `$where` on `:updated_at` is a real filter, not silently ignored. Safe to use as the incremental predicate. |
| 3 | Is `:updated_at` orderable in `$order`? | Yes. `$order=:updated_at,unique_key` returned a response sorted strictly ascending by `:updated_at`. | Stable keyset pagination on `:updated_at,unique_key` is viable, mirroring the pattern Phase 0 proved for `created_date,unique_key`. |
| 4 | Does `:updated_at` actually diverge from `created_date`? | Yes — of 5,000 rows created 2026-05-01–2026-05-08 (~3.5–4 months before the probe date), **100%** had `:updated_at` more than 1 hour after `created_date`. **But** the divergence is not fine-grained per-row tracking: only **91 distinct `:updated_at` values** appear across the 5,000-row sample, and the single most common value covers **48.94%** of the sample — consistent with a periodic (roughly daily, clustered near 01:33 UTC) batch republish process rather than an update timestamp that moves the instant a row's content changes. Direct measurement against `closed_date` for genuinely closed rows in the same window (n=4,730): `:updated_at` lags `closed_date` by **min 2.53 days, median 6.71 days, max 92.42 days**. | `:updated_at` **does** track closures — it is not near-zero divergence, so it does not fail the plan's stated kill condition. But it never reflects same-day closure (2.53-day floor observed) and has a long tail (up to ~92 days in this sample). A watermark on `:updated_at` will be reliably eventually-consistent, not fresh within the run day. This has direct implications for the Phase 6 freshness SLA target — "daily incremental" should not be read as "same-day closure visibility." |
| 5 | Are there other candidate fields? | `resolution_action_updated_date` exists among the 44 columns and looks purpose-built as a modification timestamp (not yet independently verified for `$where`/`$order` behavior — out of scope for C1.1's six questions). No `:version` field exists. The other `:`-prefixed candidates (`:@computed_region_*`) are geography lookup columns, not modification timestamps. | `resolution_action_updated_date` is a plausible secondary/cross-check field for Phase 3 quality checks, but `:updated_at` remains the only field verified here as filterable+orderable at the API level. |
| 6 | Quantify the staleness risk of a `created_date`-only watermark. | Of 294,770 rows created in the last 30 days (2026-07-21–2026-08-20), **23.37%** (68,897 rows) are still open (non-Closed) at pull time. | This is the size of the permanent-staleness problem described in phase-1.md: nearly a quarter of recently created rows would never be revisited under a pure `created_date` watermark, poisoning `resolution_hours` and any SLA analysis for that share of rows. |

**Summary:** `:updated_at` passes all four API-mechanics checks (exists, filterable,
orderable, diverges from `created_date`) and does not hit the stated failure
condition in Q4 (divergence is not near-zero). The one material caveat, not covered
by the plan's original framing, is that `:updated_at` appears to move on a periodic
batch cadence with an observed **2.5–92 day lag** behind the actual `closed_date`,
not immediately on content change. See `docs/decisions.md` for the recommendation
this produces, pending H1.1 approval.

**H1.1 outcome:** Approved with conditions (row-level-vs-batch distinction test,
three C1.4 design requirements). See C1.1b below and `docs/decisions.md`.

## C1.1b — Row-level change vs. publication-batch timestamp

Run: `.venv/bin/python scripts/probe_watermark_republish.py`, executed live on
2026-08-19/20. Purpose: distinguish whether `:updated_at` tracks genuine per-row
content changes (in which case the 91-distinct-values clustering from C1.1 is
coincidental) or advances on every periodic republish regardless of whether a row
changed (in which case that clustering is the actual mechanism).

**Method:** sample rows with `created_date` in 2021 **and** `closed_date` in 2021 —
long-settled requests with no plausible reason to have been modified recently — and
look at the distribution of their `:updated_at` values.

| Step | Observed value |
|---|---|
| Total rows created AND closed in 2021 | **3,047,736** |
| Sample pulled (page-capped) | 50,000 rows |
| `:updated_at` year distribution across the sample | **2025: 49,997 (99.99%)** · 2026: 3 (0.01%) · **2021: 0 (0.00%)** |
| Dominant year-month bucket | **2025-12: 49,997 rows (99.99%)** |

**Verdict: BATCH-REPUBLISH.** `:updated_at` for rows settled in 2021 is overwhelmingly
clustered in **December 2025** — the exact month CLAUDE.md records as the dataset's
restructuring date. This is not row-level modification tracking; it is (almost
certainly) the December 2025 migration touching `:updated_at` for effectively the
entire historical dataset in one bulk operation, unrelated to any actual content
change in these specific rows.

**Consequence per the pre-agreed decision rule:** the watermark still prevents
permanent staleness (`:updated_at` is monotonic and every row that genuinely changes
will eventually be caught), but **naive incremental-volume estimates derived from
`:updated_at` are contaminated by batch-republish noise** and cannot be trusted as a
proxy for "rows genuinely modified since the last run." The C1.7 delta-only gate
criterion ("rows added must be only those genuinely new or modified... duration must
be a small fraction of the backfill") needs redefinition before C1.4 is written,
because a future bulk republish event landing inside an incremental run's window
would make that run look like a near-full backfill through no fault of the pipeline.

### Rows-per-incremental-run estimate (Step 5)

Live daily counts of rows where `:updated_at` fell in that calendar day (UTC), for
the 7 days preceding the probe:

| Day (UTC, days before probe) | Rows with `:updated_at` in that day |
|---|---|
| 1 (2026-08-19) | **537,069** |
| 2 (2026-08-18) | 13,512 |
| 3 (2026-08-17) | 14,110 |
| 4 (2026-08-16) | 11,737 |
| 5 (2026-08-15) | 9,507 |
| 6 (2026-08-14) | 0 |
| 7 (2026-08-13) | 22,561 |

Average: **86,928 rows/day**. This number is explicitly **not reliable** as an
expected steady-state incremental volume — day 1 alone (537,069 rows, ~40x the next
highest day) looks like another bulk republish event in progress, and day 6 (zero)
shows the underlying process is not a steady daily drip either. Excluding the day-1
outlier, the remaining 6 days average **11,905 rows/day**, which is a better
first-pass planning number but still unverified as "genuine change" vs. residual
batch noise. Recorded in `docs/decisions.md` as the number criterion 7 will be
judged against, with this caveat attached.

### Timezone evidence for `created_date` (feeds requirement B)

`:updated_at` is unambiguous — every observed value carries a trailing `Z`
(offset-aware UTC). `created_date` carries no offset suffix. To determine its
implied timezone without assuming, live hour-of-day call-volume distribution was
pulled for June 2026 (`date_extract_hh(created_date)` grouped and counted):

| Hour (as stored, no shift applied) | Count |
|---|---|
| 00 | 12,562 |
| 01 | 8,145 |
| 02 | 5,200 |
| 03 | 3,874 |
| **04** | **3,378 (trough)** |
| 05 | 4,539 |
| 06 | 7,693 |
| 07 | 12,722 |
| 08 | 17,041 |
| 09–22 | 15,247–19,399 (elevated, business-hours plateau) |
| 23 | 15,247 |

The trough sits at 03:00–05:00 as-stored, matching the typical overnight lull for
any US city's *local* time — not the pattern that would appear if these values were
UTC and needed a −4/−5 hour shift to Eastern (which would place the "true" local
trough at 23:00–01:00 as-stored, an implausible time for call volume to bottom out,
or push the plateau's start into the pre-dawn hours). **Working assumption:**
`created_date` is recorded in Eastern local time (`America/New_York`), not UTC, not
shifted. This is inferential (call-volume-pattern) evidence, not a documented
guarantee from Socrata — flagged as such. Whether it is DST-aware (EDT vs. EST) or a
fixed offset is unverified and out of scope for C1.1b; not load-bearing for the
watermark itself (which runs entirely on `:updated_at`, already unambiguous UTC), but
matters for `day(created_date)` partition boundaries and any local-time SLA framing
downstream.

### Requirement C — `resolution_action_updated_date` cross-check field

| Check | Result |
|---|---|
| `$where` filtering | **Confirmed working** — bounded window (2026-08-01–2026-08-07) returned 5,000 rows, 0 outside the window. |
| `$order` sorting | **Confirmed working** — response returned strictly sorted ascending. |

`resolution_action_updated_date` is usable as a `$where`/`$order`-capable field for
C1.8's update-absorption cross-check, per requirement C. Not proposed as a
replacement for `:updated_at`.

## C1.1b follow-up — sizing the republish contamination

Run live, follow-up to C1.1b, 2026-08-20.

**Fraction of the 24-month backfill window touched by the Dec 2025 republish:**
`$where=created_date >= '2024-08-19' and :updated_at between '2025-12-01' and
'2025-12-31'` → **4,398,512 rows**, i.e. **58.56% of the 7,511,072-row backfill
window** carries a December 2025 `:updated_at` stamp. This is a one-time historical
migration artifact (it will be absorbed once, during the initial Phase 1 backfill —
it does not recur on its own), but it establishes that the republish mechanism is
capable of touching a majority of the table in a single event, not a negligible
edge case.

**New-row publish lag (why brand-new rows don't get an immediate `:updated_at`):**
Querying the 10 most recently created rows (as of 2026-08-20T00:57 UTC) shows every
one of them — created 2026-08-18, i.e. within the last ~2 days — carries an
`:updated_at` roughly **one day later**, clustered at 02:00–02:03 UTC:

```
created=2026-08-18T02:20:12.000  updated_at=2026-08-19T02:02:42.436Z
created=2026-08-18T01:51:14.000  updated_at=2026-08-19T02:03:11.493Z
created=2026-08-18T01:50:08.000  updated_at=2026-08-19T02:02:42.436Z
... (10/10 rows follow this pattern)
```

**⚠️ CORRECTED BELOW.** The paragraph above originally read `:updated_at` as "last
touched by the nightly publish cycle... appears to include every row with any
reason to be in that day's publish." Human review (`docs/decisions.md`, "Correction
1") caught that this over-generalizes: if the cycle touched every row with any
reason to be in the day's publish, daily counts would run far higher than the
observed ~11,905/day baseline (≈0.16% of the 7,511,072-row backfill). **The correct
reading: the nightly cycle batch-stamps whatever rows genuinely changed since the
last cycle**, using the batch run's own timestamp as the stamp for all of them at
once — a *batch-stamped change timestamp*, not a wholesale republish. The
2.53-day minimum closure-lag floor and the new-row publish-lag pattern above still
stand (a row must wait for the next cycle to be stamped, whether newly created or
newly closed) — what's corrected is only the claim that the cycle touches
*everything*, not just what changed.

**Composition of the most-recent single-day spike is now resolved — the spike is
real, not a boundary artifact.** See the realigned re-run below.

**Composition of the most-recent single-day spike (537,069 rows) is inconclusive
from creation-date heuristics alone** — a same-day-created vs. touched-earlier split
attempted via `created_date` boundary comparison returned a same-day count of 0, but
that is very likely a window-alignment artifact (the ~02:00–02:03 UTC nightly stamp
for a given day's new rows can fall just outside or inside a rolling 24h query
window depending on exact script execution time), not evidence that zero genuinely
new rows were involved. **Deliberately not over-interpreted here** — this is exactly
what the approved C1.7d one-time field-value-diff measurement is for, and that
measurement should be trusted over creation-date-boundary heuristics.

## C1.1b re-run — realigned to the ~02:00 UTC batch boundary

Run live 2026-08-20, per human-directed correction: the original 7-day count used
midnight-UTC-cut buckets, but the batch-stamp cluster lands at ~01:33–02:03 UTC, so a
midnight cut risks splitting one day's cluster across two buckets. Re-run with
windows anchored to 02:00 UTC:

| Window (02:00 UTC boundaries) | Rows | vs. original (midnight-cut) |
|---|---|---|
| 2026-08-18T02:00 – 2026-08-19T02:00 | **526,651** | 537,069 (−1.9%, same order of magnitude) |
| 2026-08-17T02:00 – 2026-08-18T02:00 | 13,512 | 13,512 (identical) |
| 2026-08-16T02:00 – 2026-08-17T02:00 | 14,110 | 14,110 (identical) |
| 2026-08-15T02:00 – 2026-08-16T02:00 | 11,737 | 11,737 (identical) |
| 2026-08-14T02:00 – 2026-08-15T02:00 | 9,507 | 9,507 (identical) |
| 2026-08-13T02:00 – 2026-08-14T02:00 | 0 | 0 (identical) |
| 2026-08-12T02:00 – 2026-08-13T02:00 | 22,561 | 22,561 (identical) |

**Verdict: the spike is real, not a boundary artifact.** It survived realignment
(526,651 vs. 537,069). The middle five values are unchanged by realignment,
confirming the ~11,905/day baseline (average of the four non-zero, non-spike days)
is robust — **≈0.16% of the 7,511,072-row backfill table per day**, not the ~87k/day
figure originally reported (which averaged in the unverified spike).

**Exact composition of the spike, by `:updated_at` value** (grouped, top values):

| `:updated_at` | Row count |
|---|---|
| 2026-08-19T01:33:23.553Z | **526,605** |
| 2026-08-19T01:58:09.528Z | 21 |
| 2026-08-19T01:56:55.855Z | 10 |
| (11 more values, ≤3 rows each) | 15 |

**99.99% of the spike shares one exact millisecond-precision timestamp** — the same
mechanism as every other day's dominant cluster (one batch stamp per run), just an
unusually large cohort for that specific run. This is a genuine large-cohort
batch-stamping event, not a measurement artifact. See `docs/decisions.md` for the
corrected interpretation (Correction 1: the cycle stamps what changed, not the whole
table) and the resulting C1.4 buffer/boundary decisions.

## C1.3b — PyIceberg `upsert()` no-op behavior (empirical test)

Run: `.venv/bin/python scripts/probe_upsert_noop.py`, 2026-08-20. Tests whether
`upsert()` already skips writing rows whose incoming values match the existing row,
before building a hand-rolled pre-upsert diff-filter.

| Test | `rows_updated` | `rows_inserted` | Snapshot count | Data file count |
|---|---|---|---|---|
| Initial append (3 rows) | — | — | 0 → 1 | 0 → 1 |
| Upsert byte-identical batch (3/3 rows, no-op) | **0** | 0 | **1 → 1 (unchanged)** | **1 → 1 (unchanged)** |
| Upsert mixed batch (1 changed + 2 identical) | **1** | 0 | 1 → 3 | 1 → 2 |

**Finding: PyIceberg's `upsert()` already skips no-op rows.** The byte-identical
batch produced zero writes at every level — no new snapshot, no new data file. The
mixed batch correctly isolated and wrote only the one genuinely changed row.
**No hand-rolled diff-filter is needed** — design option (ii) from the H1.1 review
is already provided by the library. C1.4's ingestion loop will upsert everything
`:updated_at` returns (scoped to touched partitions per C1.5) and rely on this
built-in behavior to absorb both steady-state volume and any future large-cohort
republish spike without redundant writes.

## C1.2 — Authoritative column list (resolves the 32-vs-44 discrepancy)

Fetched live from Socrata's dataset **metadata** endpoint,
`https://data.cityofnewyork.us/api/views/erm2-nwe9.json`, 2026-08-20 — this returns
the dataset's authoritative schema (every defined column, regardless of whether any
given row happens to populate it), unlike a row-level `$select` response.

**Authoritative column count: 48.** This fully resolves the earlier discrepancy:
- **48** — the true schema, from the metadata endpoint (below).
- **44** — H0.2's human-reported figure from the dataset landing page. `48 − 4 =
  44`: the portal's displayed column count excludes the 4 `:@computed_region_*`
  system-computed geography columns, which the metadata endpoint does include. Not
  an error in H0.2 — the portal UI and the API schema are simply counting different
  things.
- **32** — Phase 0's single-row `$select` sample. Confirmed cause: Socrata's JSON
  API omits a field from a row's response entirely when that field is null for that
  row, so any single sampled row understates the schema. Not a `$select` narrowing
  (no `$select` was used in that Phase 0 probe) and not a portal/API mismatch.

### Authoritative column list with types (bronze schema source)

| API field name | Socrata type | Iceberg/PyArrow mapping (bronze) |
|---|---|---|
| unique_key | text | string |
| created_date | calendar_date | timestamp, naive, assumed Eastern local (see Phase 1 timezone decision) |
| closed_date | calendar_date | timestamp, naive, assumed Eastern local |
| agency | text | string |
| agency_name | text | string |
| complaint_type | text | string |
| descriptor | text | string |
| descriptor_2 | text | string |
| location_type | text | string |
| incident_zip | text | string |
| incident_address | text | string |
| street_name | text | string |
| cross_street_1 | text | string |
| cross_street_2 | text | string |
| intersection_street_1 | text | string |
| intersection_street_2 | text | string |
| address_type | text | string |
| city | text | string |
| landmark | text | string |
| facility_type | text | string |
| status | text | string |
| due_date | calendar_date | timestamp, naive, assumed Eastern local |
| resolution_description | text | string |
| resolution_action_updated_date | calendar_date | timestamp, naive, assumed Eastern local (C1.8 cross-check field) |
| community_board | text | string |
| council_district | text | string |
| police_precinct | text | string |
| bbl | text | string |
| borough | text | string |
| x_coordinate_state_plane | number | double |
| y_coordinate_state_plane | number | double |
| open_data_channel_type | text | string |
| park_facility_name | text | string |
| park_borough | text | string |
| vehicle_type | text | string |
| taxi_company_borough | text | string |
| taxi_pick_up_location | text | string |
| bridge_highway_name | text | string |
| bridge_highway_direction | text | string |
| road_ramp | text | string |
| bridge_highway_segment | text | string |
| latitude | number | double |
| longitude | number | double |
| location | point (GeoJSON) | flattened to two scalar doubles, `location_lon`/`location_lat` (implementation choice made in `ingest/schema.py` — avoids a nested Arrow/Iceberg struct type for a field that's already redundant with the separate `latitude`/`longitude` scalar columns; GeoJSON `coordinates` order is `[longitude, latitude]`, extracted accordingly) |
| :@computed_region_f5dn_yrer | number | double (Community Districts region id) |
| :@computed_region_yeji_bk3q | number | double (Borough Boundaries region id) |
| :@computed_region_sbqj_enih | number | double (Police Precincts region id) |
| :@computed_region_92fq_4b7q | number | double (City Council Districts region id) |
| `:updated_at` (system field, not in the 48) | fixed_timestamp | timestamp with timezone (UTC) — the watermark field |

**Type mapping decisions:**
- All Socrata `number` columns (including the 4 `:@computed_region_*` region-id
  columns, which are small integers in practice) are mapped to `double` uniformly
  for bronze fidelity rather than guessing int vs. float per column — bronze
  prioritizes raw-payload fidelity with minimal transformation per C1.3; tighter
  typing (e.g., int32 region codes) is a Phase 2 staging-layer decision, not bronze.
  x_coordinate_state_plane/y_coordinate_state_plane look integer-valued in samples
  but are typed `number` by Socrata with no documented guarantee against decimals.
- All `calendar_date` columns are naive timestamps, treated under the same
  assumed-Eastern-local convention established for `created_date` in C1.1b's
  timezone evidence — not verified independently per-column, but there's no reason
  to expect NYC 311 mixes timezone conventions across its own date fields.
- `:updated_at` is the one genuinely offset-aware (UTC) timestamp field and is not
  part of the 48-column dataset schema — it's a Socrata system field, present on
  every row regardless of dataset-specific schema.
- `location` (GeoJSON Point) is flattened to two scalar doubles
  (`location_lon`/`location_lat`) rather than kept as a nested struct, to avoid
  nested Arrow/Iceberg types in bronze for a field that's already redundant with
  the separate `latitude`/`longitude` scalar columns — no information is lost.

## C1.9 — Data-quality re-probe, full 24-month backfill, by year

Full table (six measures × 3 years, with the two real year-over-year trends
worth flagging for Phase 3) is in `docs/metrics.md`. Run live 2026-08-20 via
DuckDB's `iceberg_scan` against the corrected, purged bronze table (7,522,072
rows). Headline: `(0,0)/out-of-bounds` coordinates are exactly 0.0000% in every
one of the three years (2024 partial, 2025 full, 2026 partial) — this defect does
not exist anywhere in the 24-month window, not just in Phase 0's single-month
sample. `missing_coords` roughly doubles from 2024 to 2026 (1.15% → 2.52%) and
`closed_null_closed_date` drops sharply in the opposite direction (0.85% →
0.0003%) — both real, both worth Phase 3 attention, neither manufactured.
