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
