{#
  Detector (e), proposed and approved at H3.2b — see docs/decisions.md,
  C3.5(e). Deliberately kept SEPARATE from detector (a): (a) is date-free
  by design so a mass touch can never move it; this detector exists
  specifically to characterize the mass-touch phenomenon itself, for Phase
  6's `:updated_at`-based freshness monitoring to account for.

  Grain: one row per hour bucket (date_trunc('hour', updated_at)) where at
  least 13 of the dataset's agencies were touched simultaneously — the
  "eligible" population. Agency-count alone is not a useful fire condition:
  ~180 of ~730 nights in the 24-month history already clear that bar as
  ordinary platform behavior (the recurring ~01:33 UTC daily signature
  documented in Phase 1's C1.7d). The discriminator is volume: fire when
  the bucket's total row count exceeds a threshold derived from the
  measured variance of the *ordinary* nights.
#}

with base as (

    select a.agency, f.updated_at
    from {{ ref('fct_service_requests') }} f
    join {{ ref('dim_agency') }} a on f.agency_key = a.agency_key

),

buckets as (

    select date_trunc('hour', updated_at) as hour_bucket, agency, count(*) as n
    from base
    group by 1, 2

),

per_bucket as (

    select hour_bucket, count(distinct agency) as distinct_agencies, sum(n) as total_rows
    from buckets
    group by 1

),

eligible as (

    -- >=13 of 16 agencies touched in the same hour (roughly 80% — the
    -- range every ordinary night already sits in per H3.2's calibration).
    select * from per_bucket
    where distinct_agencies >= 13

),

-- Threshold derived from the "ordinary" nights only (excluding the two
-- most extreme, so they don't inflate the variance used to detect them —
-- matching H3.2's methodology exactly: mean + 3*stdev of nights under
-- 100,000 rows).
ordinary_stats as (

    select
        avg(total_rows) as mean_rows,
        stddev_pop(total_rows) as stdev_rows
    from eligible
    where total_rows < 100000

),

threshold as (

    select mean_rows + 3 * stdev_rows as threshold_rows
    from ordinary_stats

)

select
    e.hour_bucket,
    e.distinct_agencies,
    e.total_rows,
    round(t.threshold_rows, 0) as threshold_rows,
    (e.total_rows > t.threshold_rows) as fired
from eligible e
cross join threshold t
order by e.hour_bucket
