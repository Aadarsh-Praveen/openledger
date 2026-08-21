{#
  Grain: one row per calendar day. Spans the 24-month backfill window
  (2024-08-19) plus headroom through end of 2027 — comfortably past any
  currently-loaded data, so incremental runs never need this rebuilt just to
  add a new day. Season attribute included since the project's compositional-
  seasonality question (C2.8) is the analytical payoff, not raw volume
  (already established as flat — Phase 1 C1.9).
#}

with spine as (

    select
        -- DuckDB promotes date + interval to TIMESTAMP, which would leave
        -- date_key mismatched against fct_service_requests.date_key (DATE) —
        -- cast back to DATE explicitly so the join key types agree exactly.
        cast(cast('2024-08-19' as date) + interval (i) day as date) as calendar_date
    from range(0, date_diff('day', cast('2024-08-19' as date), cast('2027-12-31' as date)) + 1) as t(i)

)

select
    calendar_date as date_key,
    calendar_date,
    extract(year from calendar_date) as year,
    extract(quarter from calendar_date) as quarter,
    extract(month from calendar_date) as month,
    monthname(calendar_date) as month_name,
    extract(day from calendar_date) as day_of_month,
    extract(dow from calendar_date) as day_of_week,
    dayname(calendar_date) as day_name,
    (extract(dow from calendar_date) in (0, 6)) as is_weekend,
    case
        when extract(month from calendar_date) in (12, 1, 2) then 'Winter'
        when extract(month from calendar_date) in (3, 4, 5) then 'Spring'
        when extract(month from calendar_date) in (6, 7, 8) then 'Summer'
        else 'Fall'
    end as season

from spine
