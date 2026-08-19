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
