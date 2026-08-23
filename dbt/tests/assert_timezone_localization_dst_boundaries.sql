{{ config(severity='error') }}

-- The "timezone handling at a day boundary" boundary case from C3.3,
-- implemented as a singular data test rather than a dbt unit_tests: node.
--
-- Why not a native unit test: a dbt unit test on stg_service_requests would
-- need to mock `source('bronze', 'service_requests')`, but that source is
-- declared via `meta.external_location` as a raw inline `iceberg_scan(...)`
-- expression (see models/staging/_sources.yml) — it never becomes an actual
-- catalogued relation in DuckDB, at any point, by design (the same
-- constraint Phase 2 found when a macro couldn't be called from source
-- YAML). dbt-duckdb's unit-test fixture builder needs to introspect a real
-- relation's columns/types for a source() input, and errors
-- ("Not able to get columns ... because the relation doesn't exist") since
-- there is nothing to introspect. Verified this is structural, not
-- transient, by trying with every column of the source explicitly
-- specified in the fixture — same error either way.
--
-- This test encodes the identical assertion directly against literal
-- inputs, using the exact same expression stg_service_requests applies
-- (timezone(created_date_timezone, created_date)), against DuckDB directly
-- rather than through a mocked model — runs in `dbt test`/`dbt build` like
-- any other test, with no source-introspection dependency at all.
--
-- Three cases, each verified once manually before being pinned down here:
--   1. A normal instant, safely inside EDT, localizes as expected.
--   2. 2026-11-01 01:30:00 is an AMBIGUOUS local time in America/New_York —
--      clocks fall back from 2am EDT to 1am EST, so 1:30am occurs twice.
--      DuckDB resolves it to the second occurrence (EST, -05:00), not the
--      first (EDT, -04:00) — a deterministic, documented choice, not
--      necessarily "the" correct one, since an ambiguous instant has no
--      single correct answer.
--   3. 2026-03-08 02:30:00 does NOT EXIST in America/New_York local time —
--      clocks spring forward from 2am straight to 3am. DuckDB does not
--      error; it shifts forward by the one-hour gap, landing at 3:30am
--      EDT. This matters operationally: a build must not fail outright
--      just because one row's naive timestamp happens to fall in this gap.

with cases as (

    select
        'normal_instant' as case_name,
        timezone('{{ var("created_date_timezone") }}', timestamp '2026-08-21 23:59:59') as actual,
        timestamp '2026-08-21 23:59:59-04:00' as expected

    union all

    select
        'dst_fallback_ambiguous_hour',
        timezone('{{ var("created_date_timezone") }}', timestamp '2026-11-01 01:30:00'),
        timestamp '2026-11-01 01:30:00-05:00'

    union all

    select
        'dst_springforward_nonexistent_hour',
        timezone('{{ var("created_date_timezone") }}', timestamp '2026-03-08 02:30:00'),
        timestamp '2026-03-08 03:30:00-04:00'

)

select case_name, actual, expected
from cases
where actual != expected
