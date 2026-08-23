{#
  Detector (b) — see docs/decisions.md, C3.5(b). Year-over-year, not
  trailing-average or month-over-month: both of those fire on every
  recurring seasonal swing, the exact failure mode phase-3.md warns
  against (HEAT/HOT WATER's genuine ~1%->21-23% winter swing must NOT
  fire, since it recurs every year and cancels out in a YoY comparison).

  Grain: one row per (complaint_type, month) for the top 15 complaint
  types by total historical volume, for every month that has a same-
  calendar-month prior-year comparison available (the first 12 months of
  the dataset, Aug 2024 - Jul 2025, are skipped — nothing to compare them
  against yet).
#}

with base as (

    select ct.complaint_type, f.created_date
    from {{ ref('fct_service_requests') }} f
    join {{ ref('dim_complaint_type') }} ct on f.complaint_type_key = ct.complaint_type_key

),

top_types as (

    select complaint_type
    from base
    group by 1
    order by count(*) desc
    limit 15

),

monthly as (

    select
        date_trunc('month', created_date) as month,
        complaint_type,
        count(*) as n
    from base
    where complaint_type in (select complaint_type from top_types)
    group by 1, 2

),

month_totals as (

    select date_trunc('month', created_date) as month, count(*) as total
    from base
    group by 1

),

shares as (

    select m.month, m.complaint_type, m.n, m.n * 100.0 / t.total as pct_share
    from monthly m
    join month_totals t on m.month = t.month

),

yoy as (

    select
        month,
        complaint_type,
        n,
        pct_share,
        -- Explicit same-calendar-month-prior-year join (not a naive lag()
        -- over consecutive months, which would compare to last MONTH, not
        -- last YEAR): join back exactly 12 months.
        (
            select s2.pct_share
            from shares s2
            where s2.complaint_type = shares.complaint_type
              and s2.month = shares.month - interval '12 months'
        ) as pct_share_yoy

    from shares

)

select
    month,
    complaint_type,
    n as volume,
    round(pct_share, 4) as pct_share,
    round(pct_share_yoy, 4) as pct_share_prior_year,
    round(pct_share - pct_share_yoy, 4) as yoy_delta_pp,
    (pct_share_yoy is not null and abs(pct_share - pct_share_yoy) > 5) as fired
from yoy
where pct_share_yoy is not null
order by month, complaint_type
