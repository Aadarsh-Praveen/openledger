{#
  Detector (a), REDESIGNED at H3.2 (2026-08-22) after the original
  date-grouped design was rejected — see docs/decisions.md, C3.5(a). The
  original design (group by (agency, date(updated_at))) was undermined by
  its own founding-case correction: a mass `:updated_at` touch turns a
  persistent, date-free backlog into an apparent one-day event, and would
  false-positive on any agency's own backlog the next time a platform-wide
  touch (see dq_detector_mass_touch.sql) happens to stamp it.

  This detector is deliberately date-free and agency-level: the rate of
  status='Closed' rows with a null closed_date, as a share of that agency's
  total closed rows. A mass `:updated_at` touch changes *when* a row was
  last touched, never *whether* it has a closed_date, so this metric cannot
  be moved by one.

  Grain: one row per (agency, scan_month) — a simulated monthly-scheduled
  production scan, evaluating each agency's CUMULATIVE rate over all rows
  created before that month's cutoff. This reproduces the exact H3.2
  backtest methodology (25 monthly cutoffs, Sep 2024 - Sep 2026) so the
  full backtest stays auditable by querying this model directly, not just
  asserted in the journal. The scorecard mart (fct_data_quality_checks)
  selects only the most recent scan_month per agency as "today's" reading.
#}

with base as (

    select a.agency, f.created_date, f.status, f.closed_date
    from {{ ref('fct_service_requests') }} f
    join {{ ref('dim_agency') }} a on f.agency_key = a.agency_key

),

-- Threshold derived ONCE from the full-history cross-agency distribution
-- (not re-derived per scan_month) — matching H3.2's methodology exactly.
full_history_rates as (

    select
        agency,
        count(*) filter (where status = 'Closed') as closed_rows,
        count(*) filter (where status = 'Closed' and closed_date is null) as undated_closure_rows,
        case
            when count(*) filter (where status = 'Closed') > 0
            then 100.0 * count(*) filter (where status = 'Closed' and closed_date is null)
                / count(*) filter (where status = 'Closed')
            else null
        end as rate_pct
    from base
    group by 1

),

threshold as (

    -- mean + 5*stdev of the non-DHS agencies (H3.2: 3*stdev sat below
    -- DSNY's own observed max, which would make DSNY a marginal false
    -- positive; 5*stdev clears it with real margin while staying ~2,000x
    -- below DHS's rate).
    select avg(rate_pct) + 5 * stddev_pop(rate_pct) as threshold_pct
    from full_history_rates
    where agency != 'DHS'

),

month_ends as (

    select unnest(generate_series(
        date_trunc('month', (select min(created_date) from base)) + interval '1 month',
        date_trunc('month', timezone('{{ var("created_date_timezone") }}', current_timestamp)::date) + interval '1 month',
        interval '1 month'
    )) as scan_cutoff

),

agencies as (

    select distinct agency from base

),

grid as (

    select m.scan_cutoff, a.agency
    from month_ends m
    cross join agencies a

),

evaluated as (

    select
        g.scan_cutoff,
        g.agency,
        count(*) filter (where b.status = 'Closed') as closed_rows,
        count(*) filter (where b.status = 'Closed' and b.closed_date is null) as undated_closure_rows
    from grid g
    left join base b
        on b.agency = g.agency
        and b.created_date < g.scan_cutoff
    group by 1, 2

)

select
    e.scan_cutoff as scan_month,
    e.agency,
    e.closed_rows,
    e.undated_closure_rows,
    case
        when e.closed_rows > 0 then round(100.0 * e.undated_closure_rows / e.closed_rows, 4)
        else null
    end as undated_closure_rate_pct,
    round(t.threshold_pct, 6) as threshold_pct,
    (e.closed_rows > 0 and (100.0 * e.undated_closure_rows / e.closed_rows) > t.threshold_pct) as fired
from evaluated e
cross join threshold t
order by e.scan_cutoff, e.agency
