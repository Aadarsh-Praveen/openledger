{#
  Detector (c) — see docs/decisions.md, C3.5(c). A notification, not a
  pass/fail check, per phase-3.md: reports every complaint_type, descriptor,
  or agency value whose first-seen created_date falls after the initial
  2024-08 backfill month (which trivially "introduces" the entire starting
  vocabulary and would otherwise dominate every count).

  Grain: one row per (dimension, value) that first appeared after the
  backfill month, with its first-seen month and total volume to date.
#}

with base as (

    select
        a.agency,
        ct.complaint_type,
        ct.descriptor,
        f.created_date
    from {{ ref('fct_service_requests') }} f
    join {{ ref('dim_agency') }} a on f.agency_key = a.agency_key
    join {{ ref('dim_complaint_type') }} ct on f.complaint_type_key = ct.complaint_type_key

),

backfill_cutoff as (

    select date_trunc('month', min(created_date)) + interval '1 month' as cutoff
    from base

),

agencies as (

    select
        'agency' as dimension,
        agency as value,
        date_trunc('month', min(created_date)) as first_seen_month,
        count(*) as volume_to_date
    from base
    group by 1, 2

),

complaint_types as (

    select
        'complaint_type' as dimension,
        complaint_type as value,
        date_trunc('month', min(created_date)) as first_seen_month,
        count(*) as volume_to_date
    from base
    group by 1, 2

),

descriptors as (

    select
        'descriptor' as dimension,
        descriptor as value,
        date_trunc('month', min(created_date)) as first_seen_month,
        count(*) as volume_to_date
    from base
    where descriptor is not null
    group by 1, 2

),

unioned as (

    select * from agencies
    union all
    select * from complaint_types
    union all
    select * from descriptors

)

select
    dimension,
    value,
    first_seen_month,
    volume_to_date
from unioned, backfill_cutoff
where first_seen_month >= backfill_cutoff.cutoff
order by dimension, first_seen_month, value
