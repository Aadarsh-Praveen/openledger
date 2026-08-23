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

  CORRECTION (2026-08-24, found via user review after Phase 3 was
  otherwise accepted): the threshold was originally computed ONCE from
  the full-history (i.e. TODAY's) cross-agency distribution and applied
  retroactively to all 25 historical scan_months — a look-ahead bug, not
  a framing choice. A real monitor running in April 2025 could not have
  known what the cross-agency distribution would look like in September
  2026. Fixed to compute the threshold POINT-IN-TIME, independently at
  each scan_month, using only data with created_date before that same
  cutoff (mean + 5*stdev of that scan_month's own non-DHS agency rates).
  Verified this changes the outcome: DSNY's 10/25 firings under the old
  static threshold become 0/25 under the point-in-time one, while DHS is
  unaffected (still 25/25 — its margin is large enough at every single
  historical checkpoint, not just today, for this to matter). Full
  detail in docs/decisions.md, C3.5(a) REDESIGNED — second correction.
#}

with base as (

    select a.agency, f.created_date, f.status, f.closed_date
    from {{ ref('fct_service_requests') }} f
    join {{ ref('dim_agency') }} a on f.agency_key = a.agency_key

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

),

rates as (

    select
        scan_cutoff,
        agency,
        closed_rows,
        undated_closure_rows,
        case
            when closed_rows > 0 then 100.0 * undated_closure_rows / closed_rows
            else null
        end as rate_pct
    from evaluated

),

-- Threshold recomputed INDEPENDENTLY at each scan_month, using only
-- non-DHS agency rates as of that same scan_month — no look-ahead into
-- later months. mean + 5*stdev, same formula as the original design,
-- just no longer computed once from today's endpoint and reused
-- retroactively (the bug this correction fixes).
threshold as (

    select
        scan_cutoff,
        avg(rate_pct) + 5 * stddev_pop(rate_pct) as threshold_pct
    from rates
    where agency != 'DHS'
    group by 1

)

select
    r.scan_cutoff as scan_month,
    r.agency,
    r.closed_rows,
    r.undated_closure_rows,
    round(r.rate_pct, 4) as undated_closure_rate_pct,
    round(t.threshold_pct, 6) as threshold_pct,
    coalesce(r.rate_pct > t.threshold_pct, false) as fired
from rates r
join threshold t on r.scan_cutoff = t.scan_cutoff
order by r.scan_cutoff, r.agency
