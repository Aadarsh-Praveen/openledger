{#
  Detector (d) — see docs/decisions.md, C3.5(d) and docs/findings.md's
  completeness-curve section. Recomputes completeness-at-45-days (% of a
  cohort's closed rows where date_diff('day', created_date, updated_at) <=
  45, per the original H2.2 measurement method) for every eligible cohort
  month, on a rolling basis.

  Eligible = at least 90 days old (Phase 2's own criterion: needs the tail
  clear to mean anything — see docs/findings.md, "a cohort that hasn't
  existed for 45 days yet can only contain rows that closed fast, by
  definition") AND not contaminated:
    - created_date before the Dec 2025 Socrata migration (2025-12-26) is
      structurally unusable — any closure predating the migration has its
      updated_at overwritten by the migration touch, not by the genuine
      closure event, corrupting the date_diff measurement regardless of
      how the cohort itself performed. Excluded via a hardcoded cutoff,
      not re-detected each run, because the migration is a known one-time
      historical event, not a recurring pattern this query re-derives.
    - January 2026 is separately excluded: 91,010 of 338,952 closed rows
      (26.9%) share one exact updated_at re-touch timestamp
      (2026-08-20), inflating that cohort's measured days-to-observable
      independent of the migration. Hardcoded per docs/findings.md's own
      finding; not generically re-detected here either.
  A future contamination of this shape would need to be found (e.g. via
  the mass-touch detector, dq_detector_mass_touch.sql) and added here
  explicitly — this model does not claim to auto-discover new ones.
#}

with base as (

    select created_date, updated_at, status
    from {{ ref('fct_service_requests') }}
    where status = 'Closed'

),

cohorts as (

    select
        date_trunc('month', created_date) as cohort_month,
        count(*) as closed_rows,
        count(*) filter (where date_diff('day', created_date, updated_at) <= 45) as observable_by_45d
    from base
    group by 1

),

eligible as (

    select
        cohort_month,
        closed_rows,
        observable_by_45d,
        round(100.0 * observable_by_45d / nullif(closed_rows, 0), 2) as completeness_pct_45d
    from cohorts
    where cohort_month >= date '2025-12-27'  -- after the Dec 2025 migration
      and cohort_month != date '2026-01-01'  -- separately contaminated, see docstring
      and cohort_month
          <= date_trunc('month', timezone('{{ var("created_date_timezone") }}', current_timestamp)::date - interval '90 days')

)

select
    cohort_month,
    closed_rows,
    observable_by_45d,
    completeness_pct_45d,
    90.0 as warn_threshold_pct,
    85.0 as fail_threshold_pct,
    case
        when completeness_pct_45d < 85.0 then 'fail'
        when completeness_pct_45d < 90.0 then 'warn'
        else 'pass'
    end as status
from eligible
order by cohort_month
